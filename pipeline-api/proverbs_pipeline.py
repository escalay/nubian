#!/usr/bin/env python3
"""
Pipeline for Nubian Proverbs (Fadijja/Mahas) by Maher Habbob.

Born-digital PDF. Each proverb has a consistent 4-part structure:
  (N)                        ← numbered entry
  OLD NUBIAN SCRIPT LINE     ← Nubian characters
  Latin transliteration.     ← italicized
  English literal translation.
  [Contextual meaning.]      ← in brackets

Usage:
    python proverbs_pipeline.py \
      -i "../books/Nubian Proverbs (Fadijja_Mahas) - Maher Habbob.pdf" \
      -o ../output/proverbs
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from unified_schema import NubianEntry, DialectForm, SourceRef, save_entries


# ─────────────────────────────────────────────────────────────
# OCR
# ─────────────────────────────────────────────────────────────

def ocr_pages(pdf_path: Path, output_dir: Path, page_start: int, page_end: int,
              mode: str = "balanced", batch_size: int = 15):
    load_dotenv("local.env")
    api_key = os.environ.get("DATALAB_API_KEY")
    if not api_key:
        print("Error: DATALAB_API_KEY not set"); sys.exit(1)
    os.environ["DATALAB_API_KEY"] = api_key

    from datalab_sdk import DatalabClient, ConvertOptions
    client = DatalabClient()

    html_dir = output_dir / "html"
    html_dir.mkdir(parents=True, exist_ok=True)

    for batch_start in range(page_start, page_end + 1, batch_size):
        batch_end = min(batch_start + batch_size - 1, page_end)

        all_exist = all(
            (html_dir / f"page_{p:04d}.html").exists()
            for p in range(batch_start, batch_end + 1)
        )
        if all_exist:
            print(f"  Pages {batch_start}-{batch_end} — cached")
            continue

        print(f"  Pages {batch_start}-{batch_end}...", end=" ", flush=True)
        t0 = time.time()

        api_range = f"{batch_start - 1}-{batch_end - 1}"
        options = ConvertOptions(
            output_format="html",
            mode=mode,
            page_range=api_range,
            paginate=True,
            disable_image_extraction=True,
        )

        result = client.convert(str(pdf_path), options=options)
        html = result.html or ""

        pattern = re.compile(
            r'<div\s+class="page"\s+data-page-id="(\d+)">(.*?)</div>\s*(?=<div\s+class="page"|</body>)',
            re.DOTALL,
        )
        pages = {}
        for match in pattern.finditer(html):
            pages[int(match.group(1))] = match.group(2).strip()

        if not pages and html.strip():
            pages[batch_start - 1] = html

        for idx, api_id in enumerate(sorted(pages.keys())):
            actual_page = batch_start + idx
            (html_dir / f"page_{actual_page:04d}.html").write_text(
                pages[api_id], encoding="utf-8"
            )

        dt = time.time() - t0
        print(f"OK ({dt:.1f}s, {len(pages)} pages)")

    return html_dir


# ─────────────────────────────────────────────────────────────
# Parser
# ─────────────────────────────────────────────────────────────

def strip_html(html: str) -> str:
    """Remove HTML tags, collapse whitespace."""
    text = re.sub(r'<[^>]+>', '\n', html)
    text = re.sub(r'\n\s*\n', '\n', text)
    return text.strip()


def parse_proverbs_page(html: str, page_number: int) -> list[NubianEntry]:
    """Parse numbered proverbs from a page's HTML."""
    text = strip_html(html)
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    entries = []
    i = 0

    while i < len(lines):
        # Look for numbered entry: (N) or just a number
        number_match = re.match(r'^\((\d+)\)$', lines[i])
        if not number_match:
            i += 1
            continue

        proverb_num = int(number_match.group(1))
        i += 1

        # Collect the next 2-5 lines as parts of this proverb
        # Line 1: Old Nubian script (all caps Nubian characters or mixed)
        # Line 2: Latin transliteration (italicized in HTML, often ends with period)
        # Line 3+: English literal translation (may span multiple lines)
        # Last: [Contextual meaning in brackets]

        nubian_script = ""
        transliteration = ""
        english_literal = ""
        contextual_meaning = ""

        parts = []
        while i < len(lines) and not re.match(r'^\(\d+\)$', lines[i]):
            parts.append(lines[i])
            i += 1

        if not parts:
            continue

        # Heuristic: first line is Nubian script if it contains Nubian-specific
        # characters or is the first non-number line
        if parts:
            nubian_script = parts[0]

        # Second line is transliteration (often italic in original)
        if len(parts) >= 2:
            transliteration = parts[1]

        # Remaining lines before brackets = English literal
        # Lines in [brackets] = contextual meaning
        english_parts = []
        meaning_parts = []
        in_brackets = False

        for part in parts[2:]:
            if part.startswith('[') or in_brackets:
                in_brackets = True
                meaning_parts.append(part.strip('[]'))
                if part.endswith(']'):
                    in_brackets = False
            else:
                english_parts.append(part)

        english_literal = " ".join(english_parts)
        contextual_meaning = " ".join(meaning_parts)

        # Extract headword from transliteration (first meaningful word)
        headword = transliteration.split('.')[0].split(',')[0].strip() if transliteration else f"proverb_{proverb_num}"

        entry = NubianEntry(
            id=f"proverbs_{page_number}_{proverb_num}",
            entry_type="proverb",
            headword=headword,
            proverb_nubian_script=nubian_script,
            proverb_transliteration=transliteration,
            proverb_literal=english_literal,
            proverb_meaning=contextual_meaning,
            proverb_text=transliteration,  # primary text form
            english=[english_literal] if english_literal else [],
            forms=[DialectForm(
                dialect="FM",
                romanization=transliteration,
            )] if transliteration else [],
            sources=[SourceRef(book="proverbs", page=page_number)],
        )

        entries.append(entry)

    return entries


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Nubian Proverbs Pipeline")
    parser.add_argument("--input", "-i", required=True)
    parser.add_argument("--output", "-o", required=True)
    parser.add_argument("--page-range", default="23-143")
    parser.add_argument("--mode", default="balanced", choices=["fast", "balanced", "accurate"])
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    pdf_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    parts = args.page_range.split("-")
    page_start = int(parts[0])
    page_end = int(parts[1])

    print(f"Nubian Proverbs Pipeline")
    print(f"  PDF: {pdf_path}")
    print(f"  Pages: {page_start}-{page_end}")
    print()

    # Stage 1: OCR
    if not args.skip_ocr:
        print("Stage 1: OCR via Datalab API...")
        ocr_pages(pdf_path, output_dir, page_start, page_end, args.mode)
    else:
        print("Stage 1: Skipped")
    print()

    # Stage 2: Parse
    print("Stage 2: Parsing proverbs...")
    html_dir = output_dir / "html"
    all_entries = []

    for p in range(page_start, page_end + 1):
        html_file = html_dir / f"page_{p:04d}.html"
        if not html_file.exists():
            continue
        html = html_file.read_text(encoding="utf-8")
        entries = parse_proverbs_page(html, p)
        all_entries.extend(entries)
        if entries:
            nums = [int(e.id.split("_")[-1]) for e in entries]
            print(f"  page_{p:04d}.html: {len(entries)} proverbs (#{min(nums)}-{max(nums)})")

    # Clean newlines
    for e in all_entries:
        e.proverb_nubian_script = re.sub(r'\s+', ' ', e.proverb_nubian_script).strip()
        e.proverb_transliteration = re.sub(r'\s+', ' ', e.proverb_transliteration).strip()
        e.proverb_literal = re.sub(r'\s+', ' ', e.proverb_literal).strip()
        e.proverb_meaning = re.sub(r'\s+', ' ', e.proverb_meaning).strip()
        e.proverb_text = e.proverb_transliteration

    # Stage 3: Save
    print(f"\nStage 3: Saving {len(all_entries)} proverbs...")
    output_path = str(output_dir / "proverbs_parsed.json")
    save_entries(all_entries, output_path, "proverbs")

    # Stats
    with_meaning = sum(1 for e in all_entries if e.proverb_meaning)
    with_script = sum(1 for e in all_entries if e.proverb_nubian_script)
    with_english = sum(1 for e in all_entries if e.proverb_literal)
    print(f"\n  With Nubian script: {with_script}")
    print(f"  With English literal: {with_english}")
    print(f"  With contextual meaning: {with_meaning}")

    # Check for missing numbers
    nums = sorted(int(e.id.split("_")[-1]) for e in all_entries)
    if nums:
        expected = set(range(nums[0], nums[-1] + 1))
        missing = expected - set(nums)
        if missing:
            print(f"  Missing proverb numbers: {sorted(missing)[:20]}{'...' if len(missing) > 20 else ''}")
        print(f"  Range: #{nums[0]}-{nums[-1]} ({len(nums)} found)")


if __name__ == "__main__":
    main()
