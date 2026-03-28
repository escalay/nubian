#!/usr/bin/env python3
"""
Stage 1 (API): OCR the Dongolese Nubian Lexicon via the Datalab Convert API.

Uses the Datalab Python SDK which handles polling automatically.
Much faster than local Chandra-OCR — processes pages in seconds, not minutes.

Usage:
    # Test on 3 pages
    python stage1_ocr_api.py \
      -i "../books/Dongolese Nubian_ A Lexicon - Charles Hubert Armbruster (1).pdf" \
      -o ../output/armbruster/html \
      --page-range 18-20

    # Full Nubian-English section
    python stage1_ocr_api.py \
      -i "../books/Dongolese Nubian_ A Lexicon - Charles Hubert Armbruster (1).pdf" \
      -o ../output/armbruster/html \
      --page-range 18-222

    # Full English-Nubian section
    python stage1_ocr_api.py \
      -i "../books/Dongolese Nubian_ A Lexicon - Charles Hubert Armbruster (1).pdf" \
      -o ../output/armbruster/html \
      --page-range 223-286 \
      --resume

Requirements:
    pip install datalab-python-sdk python-dotenv
    DATALAB_API_KEY in local.env or environment
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


BATCH_SIZE = 10  # pages per API request


def load_api_key() -> str:
    load_dotenv("local.env")
    key = os.environ.get("DATALAB_API_KEY")
    if not key:
        print("Error: DATALAB_API_KEY not found. Set it in local.env or environment.")
        sys.exit(1)
    return key


def split_html_pages(html: str) -> dict[int, str]:
    """
    Split paginated HTML into per-page content.

    The API wraps each page in <div class="page" data-page-id="N">.
    Returns dict mapping page_id -> inner HTML.
    """
    pages = {}
    pattern = re.compile(
        r'<div\s+class="page"\s+data-page-id="(\d+)">(.*?)</div>\s*(?=<div\s+class="page"|</body>)',
        re.DOTALL,
    )
    for match in pattern.finditer(html):
        page_id = int(match.group(1))
        pages[page_id] = match.group(2).strip()

    if not pages and html.strip():
        pages[0] = html

    return pages


def main():
    parser = argparse.ArgumentParser(
        description="Stage 1 (API): OCR via the Datalab Convert API"
    )
    parser.add_argument("--input", "-i", required=True, help="Path to the lexicon PDF")
    parser.add_argument("--output", "-o", required=True, help="Output directory for HTML pages")
    parser.add_argument(
        "--page-range", type=str, required=True,
        help="Page range, 1-indexed (e.g., '18-20', '18-222')",
    )
    parser.add_argument(
        "--mode", choices=["fast", "balanced", "accurate"], default="accurate",
        help="Processing mode: fast (simple docs), balanced (recommended), accurate (scanned/dense). Default: accurate",
    )
    parser.add_argument(
        "--batch-size", type=int, default=BATCH_SIZE,
        help=f"Pages per API request (default: {BATCH_SIZE})",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip batches where all output pages already exist",
    )
    args = parser.parse_args()

    pdf_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not pdf_path.exists():
        print(f"Error: Input file not found: {pdf_path}")
        sys.exit(1)

    api_key = load_api_key()
    os.environ["DATALAB_API_KEY"] = api_key

    from datalab_sdk import DatalabClient, ConvertOptions

    client = DatalabClient()

    # Parse 1-indexed page range
    range_parts = args.page_range.split("-")
    range_start = int(range_parts[0])
    range_end = int(range_parts[1]) if len(range_parts) > 1 else range_start
    total_pages = range_end - range_start + 1

    print(f"Processing pages {range_start}-{range_end} ({total_pages} pages)")
    print(f"Batch size: {args.batch_size} | Mode: {args.mode}")
    print()

    all_results = []
    start_time = time.time()
    batch_num = 0

    for batch_start in range(range_start, range_end + 1, args.batch_size):
        batch_end = min(batch_start + args.batch_size - 1, range_end)
        batch_num += 1
        batch_pages = batch_end - batch_start + 1

        # Resume: skip if all pages in batch exist
        if args.resume:
            all_exist = all(
                (output_dir / f"page_{p:04d}.html").exists()
                for p in range(batch_start, batch_end + 1)
            )
            if all_exist:
                print(f"  [Batch {batch_num}] Pages {batch_start}-{batch_end} — skipped (exists)")
                continue

        print(
            f"  [Batch {batch_num}] Pages {batch_start}-{batch_end} ({batch_pages} pages)...",
            end=" ", flush=True,
        )
        batch_start_time = time.time()

        try:
            # API uses 0-indexed pages
            api_range = f"{batch_start - 1}-{batch_end - 1}"

            options = ConvertOptions(
                output_format="html",
                mode=args.mode,
                page_range=api_range,
                paginate=True,
                disable_image_extraction=True,  # dictionary has no images we need
                additional_config={
                    "keep_pageheader_in_output": True,  # keep headers for validation
                    "keep_pagefooter_in_output": False,
                },
            )

            # SDK handles polling automatically
            result = client.convert(str(pdf_path), options=options)

            batch_time = time.time() - batch_start_time
            html = result.html or ""
            quality = result.parse_quality_score

            # Split into individual page files
            pages = split_html_pages(html)

            if not pages:
                out_path = output_dir / f"pages_{batch_start:04d}_{batch_end:04d}.html"
                out_path.write_text(html, encoding="utf-8")
                print(f"OK ({batch_time:.1f}s, saved as single file, quality={quality})")
            else:
                saved = 0
                api_page_ids = sorted(pages.keys())
                for idx, api_id in enumerate(api_page_ids):
                    actual_page = batch_start + idx
                    page_html = pages[api_id]

                    (output_dir / f"page_{actual_page:04d}.html").write_text(
                        page_html, encoding="utf-8"
                    )

                    meta = {
                        "page_number": actual_page,
                        "api_page_id": api_id,
                        "html_length": len(page_html),
                        "quality_score": quality,
                        "processing_time_seconds": round(batch_time / len(api_page_ids), 2),
                    }
                    (output_dir / f"page_{actual_page:04d}_meta.json").write_text(
                        json.dumps(meta, indent=2), encoding="utf-8"
                    )
                    saved += 1

                print(f"OK ({batch_time:.1f}s, {saved} pages, quality={quality})")
                all_results.append({
                    "batch": batch_num,
                    "pages": f"{batch_start}-{batch_end}",
                    "time_seconds": round(batch_time, 2),
                    "pages_saved": saved,
                    "quality_score": quality,
                })

        except Exception as e:
            batch_time = time.time() - batch_start_time
            print(f"ERROR ({batch_time:.1f}s): {e}")
            all_results.append({
                "batch": batch_num,
                "pages": f"{batch_start}-{batch_end}",
                "error": str(e),
            })

    # Save manifest
    total_time = time.time() - start_time
    manifest = {
        "input_file": str(pdf_path),
        "page_range": f"{range_start}-{range_end}",
        "total_pages": total_pages,
        "mode": args.mode,
        "total_time_seconds": round(total_time, 2),
        "batches": all_results,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    print(f"\n{'='*60}")
    print(f"OCR complete!")
    print(f"  Pages: {range_start}-{range_end} ({total_pages} total)")
    print(f"  Total time: {total_time:.1f}s ({total_time/max(total_pages,1):.1f}s/page avg)")
    print(f"  Output: {output_dir}")
    print(f"\nNext step: python ../pipeline/stage2_parse.py -i {output_dir} -o ./lexicon.json --validate --pretty")


if __name__ == "__main__":
    main()
