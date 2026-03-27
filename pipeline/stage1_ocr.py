#!/usr/bin/env python3
"""
Stage 1: OCR the Dongolese Nubian Lexicon using Chandra OCR.

This script converts the scanned PDF pages into HTML with preserved
bold/italic formatting, which is essential for extracting dictionary structure.

Run on a machine with GPU (e.g., M4 Max Mac with MPS).

Usage:
    # Process the full lexicon (dictionary pages only, ~page 18-278)
    python stage1_ocr.py --input "path/to/lexicon.pdf" --output ./ocr_output

    # Test on a small range first
    python stage1_ocr.py --input "path/to/lexicon.pdf" --output ./ocr_output --page-range 18-20

    # Use vLLM server if you have one running
    python stage1_ocr.py --input "path/to/lexicon.pdf" --output ./ocr_output --method vllm

Requirements:
    pip install 'chandra-ocr[hf]'
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path


def setup_mps():
    """Configure PyTorch for Apple Silicon MPS backend."""
    import torch
    if torch.backends.mps.is_available():
        print("✓ Apple MPS (Metal Performance Shaders) backend detected")
        os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.0")
        return "mps"
    elif torch.cuda.is_available():
        print(f"✓ CUDA GPU detected: {torch.cuda.get_device_name(0)}")
        return "cuda"
    else:
        print("⚠ No GPU detected, falling back to CPU (will be very slow)")
        return "cpu"


def parse_page_range(page_range_str: str, total_pages: int) -> list[int]:
    """Parse page range string like '18-20,25,30-35' into list of 0-indexed page numbers."""
    pages = []
    for part in page_range_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            start, end = int(start), int(end)
            pages.extend(range(start - 1, min(end, total_pages)))  # Convert to 0-indexed
        else:
            page_num = int(part) - 1  # Convert to 0-indexed
            if 0 <= page_num < total_pages:
                pages.append(page_num)
    return sorted(set(pages))


def main():
    parser = argparse.ArgumentParser(description="Stage 1: OCR the Dongolese Nubian Lexicon")
    parser.add_argument("--input", "-i", required=True, help="Path to the lexicon PDF")
    parser.add_argument("--output", "-o", required=True, help="Output directory for OCR results")
    parser.add_argument("--method", choices=["hf", "vllm"], default="hf",
                        help="Inference method: 'hf' for local model (default), 'vllm' for server")
    parser.add_argument("--page-range", type=str, default=None,
                        help="Page range to process (e.g., '18-20,25'). Default: all pages")
    parser.add_argument("--batch-size", type=int, default=1,
                        help="Batch size for HF inference (default: 1 for MPS/CPU)")
    parser.add_argument("--max-tokens", type=int, default=12384,
                        help="Maximum output tokens per page")
    parser.add_argument("--resume", action="store_true",
                        help="Skip pages that already have output files")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)

    # Detect device
    if args.method == "hf":
        device = setup_mps()
        if device == "mps":
            os.environ["TORCH_DEVICE"] = "mps"

    # Load PDF pages as images
    print(f"\nLoading PDF: {input_path}")
    from chandra.input import load_file

    config = {}
    if args.page_range:
        config["page_range"] = args.page_range

    images = load_file(str(input_path), config)
    total_pages = len(images)
    print(f"Loaded {total_pages} page(s)")

    # Determine which pages to process
    if args.page_range:
        page_numbers = parse_page_range(args.page_range, total_pages + 100)
        # Map to actual loaded image indices
        page_labels = []
        for i, _ in enumerate(images):
            # The page_range was already applied by load_file, so images[i] corresponds
            # to the i-th page in the range
            page_labels.append(i)
    else:
        page_labels = list(range(total_pages))

    # Load model
    print(f"\nLoading Chandra model (method: {args.method})...")
    from chandra.model import InferenceManager
    model = InferenceManager(method=args.method)
    print("✓ Model loaded successfully")

    # Custom prompt optimized for dictionary OCR
    # We want the standard OCR layout prompt which preserves bold/italic
    from chandra.model.schema import BatchInputItem

    # Process pages
    results_manifest = []
    start_time = time.time()

    for idx, image in enumerate(images):
        page_label = page_labels[idx]
        page_file = output_dir / f"page_{page_label:04d}"

        # Resume support: skip if output exists
        if args.resume and (page_file.with_suffix(".html")).exists():
            print(f"  [Skipping] Page {page_label + 1} (already processed)")
            continue

        print(f"  [{idx + 1}/{total_pages}] Processing page {page_label + 1}...", end=" ", flush=True)
        page_start = time.time()

        try:
            batch = [BatchInputItem(image=image, prompt_type="ocr_layout")]
            results = model.generate(
                batch,
                max_output_tokens=args.max_tokens,
                include_images=False,
                include_headers_footers=True,  # Keep headers for validation
            )

            result = results[0]
            page_time = time.time() - page_start

            # Save HTML output
            html_path = output_dir / f"page_{page_label:04d}.html"
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(result.html)

            # Save markdown output
            md_path = output_dir / f"page_{page_label:04d}.md"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(result.markdown)

            # Save metadata
            meta_path = output_dir / f"page_{page_label:04d}_meta.json"
            meta = {
                "page_number": page_label + 1,
                "token_count": result.token_count,
                "num_chunks": len(result.chunks),
                "processing_time_seconds": round(page_time, 2),
                "page_box": result.page_box,
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)

            results_manifest.append(meta)
            print(f"OK ({page_time:.1f}s, {result.token_count} tokens)")

        except Exception as e:
            page_time = time.time() - page_start
            print(f"ERROR ({page_time:.1f}s): {e}")
            results_manifest.append({
                "page_number": page_label + 1,
                "error": str(e),
                "processing_time_seconds": round(page_time, 2),
            })

    # Save manifest
    total_time = time.time() - start_time
    manifest = {
        "input_file": str(input_path),
        "total_pages_processed": len(results_manifest),
        "total_time_seconds": round(total_time, 2),
        "pages": results_manifest,
    }
    manifest_path = output_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n{'='*60}")
    print(f"OCR complete!")
    print(f"  Pages processed: {len(results_manifest)}")
    print(f"  Total time: {total_time:.1f}s ({total_time/max(len(results_manifest),1):.1f}s/page avg)")
    print(f"  Output directory: {output_dir}")
    print(f"  Manifest: {manifest_path}")
    print(f"\nNext step: run stage2_parse.py on the output directory")


if __name__ == "__main__":
    main()
