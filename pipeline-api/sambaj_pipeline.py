#!/usr/bin/env python3
"""
Full pipeline for the Sambaj Nubian Dictionary (القاموس النوبي).

Stages:
  1. Extract page screenshots from PDF (for visual reference)
  2. OCR via Datalab API (HTML with table detection)
  3. Parse HTML tables into structured JSON entries

Usage:
    # Process all dictionary pages
    python sambaj_pipeline.py \
      -i "../books/alqamws alnwby Nubian Dictionary - ywsf smbaj.pdf" \
      -o ../output/sambaj

    # Test on a few pages
    python sambaj_pipeline.py \
      -i "../books/alqamws alnwby Nubian Dictionary - ywsf smbaj.pdf" \
      -o ../output/sambaj \
      --page-range 20-34

Requirements:
    pip install datalab-python-sdk python-dotenv pypdfium2 pillow
    DATALAB_API_KEY in local.env or environment
"""

import argparse
import json
import os
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from sambaj_schema import DictionaryEntry, METADATA, split_comma, split_hyphen


# ─────────────────────────────────────────────────────────────
# Stage 1: Screenshot Extraction
# ─────────────────────────────────────────────────────────────

def extract_screenshots(pdf_path: Path, output_dir: Path, page_start: int, page_end: int):
    """Extract each page as a PNG image for visual reference."""
    import pypdfium2 as pdfium

    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    pdf = pdfium.PdfDocument(str(pdf_path))
    total = len(pdf)
    extracted = 0

    for page_num in range(page_start - 1, min(page_end, total)):
        out_path = screenshots_dir / f"page_{page_num + 1:04d}.png"
        if out_path.exists():
            continue

        page = pdf[page_num]
        # Render at 2x for good readability
        bitmap = page.render(scale=2)
        image = bitmap.to_pil()
        image.save(str(out_path), "PNG")
        extracted += 1

    print(f"  Screenshots: {extracted} new, {output_dir / 'screenshots'}")
    return screenshots_dir


# ─────────────────────────────────────────────────────────────
# Stage 2: OCR via Datalab API
# ─────────────────────────────────────────────────────────────

def ocr_pages(pdf_path: Path, output_dir: Path, page_start: int, page_end: int,
              mode: str = "accurate", batch_size: int = 10):
    """OCR dictionary pages via Datalab API, returns per-page HTML files."""
    load_dotenv("local.env")
    api_key = os.environ.get("DATALAB_API_KEY")
    if not api_key:
        print("Error: DATALAB_API_KEY not set")
        sys.exit(1)
    os.environ["DATALAB_API_KEY"] = api_key

    from datalab_sdk import DatalabClient, ConvertOptions
    client = DatalabClient()

    html_dir = output_dir / "html"
    html_dir.mkdir(parents=True, exist_ok=True)

    total_pages = page_end - page_start + 1
    batch_num = 0

    for batch_start in range(page_start, page_end + 1, batch_size):
        batch_end = min(batch_start + batch_size - 1, page_end)
        batch_num += 1

        # Skip if all pages exist
        all_exist = all(
            (html_dir / f"page_{p:04d}.html").exists()
            for p in range(batch_start, batch_end + 1)
        )
        if all_exist:
            print(f"  [Batch {batch_num}] Pages {batch_start}-{batch_end} — cached")
            continue

        print(f"  [Batch {batch_num}] Pages {batch_start}-{batch_end}...", end=" ", flush=True)
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

        # Split paginated HTML into per-page files
        pages = {}
        pattern = re.compile(
            r'<div\s+class="page"\s+data-page-id="(\d+)">(.*?)</div>\s*(?=<div\s+class="page"|</body>)',
            re.DOTALL,
        )
        for match in pattern.finditer(html):
            pages[int(match.group(1))] = match.group(2).strip()

        if not pages and html.strip():
            pages[batch_start - 1] = html

        saved = 0
        for idx, api_id in enumerate(sorted(pages.keys())):
            actual_page = batch_start + idx
            (html_dir / f"page_{actual_page:04d}.html").write_text(
                pages[api_id], encoding="utf-8"
            )
            saved += 1

        dt = time.time() - t0
        print(f"OK ({dt:.1f}s, {saved} pages)")

    print(f"  OCR output: {html_dir}")
    return html_dir


# ─────────────────────────────────────────────────────────────
# Stage 3: HTML Table Parser
# ─────────────────────────────────────────────────────────────

class TableParser(HTMLParser):
    """Extract rows from HTML tables."""

    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []  # tables > rows > cells
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_row: list[str] = []
        self._current_cell = ""
        self._current_table: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._in_table = True
            self._current_table = []
        elif tag == "tr" and self._in_table:
            self._in_row = True
            self._current_row = []
        elif tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._current_cell = ""
        elif tag == "br" and self._in_cell:
            self._current_cell += " "

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            self._current_row.append(self._current_cell.strip())
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._current_row:
                self._current_table.append(self._current_row)
        elif tag == "table" and self._in_table:
            self._in_table = False
            if self._current_table:
                self.tables.append(self._current_table)

    def handle_data(self, data):
        if self._in_cell:
            self._current_cell += data


def detect_letter_section(html: str, first_english_word: str = "") -> Optional[str]:
    """Detect the letter section from (A)/(B) headers or first English entry."""
    # Try explicit header first
    match = re.search(r'\(([A-Z])\)', html)
    if match:
        return match.group(1)

    # Fallback: infer from the first English word on the page
    if first_english_word:
        clean = re.sub(r'^[^a-zA-Z]*', '', first_english_word)
        if clean:
            return clean[0].upper()

    return None


HEADER_KEYWORDS = {
    "In English", "In (K-D)", "In (F-M)", "العربية", "الكنزية", "الفديجا",
    # Sub-header rows from rowspan splits
    "الدنقلاوية", "المحسية", "المحسبة",
}


def is_header_row(row: list[str]) -> bool:
    """Check if a table row is a column header, not data."""
    text = " ".join(row)
    # Exact match for sub-headers (rows with only these Arabic words)
    if all(cell.strip() in HEADER_KEYWORDS or not cell.strip() for cell in row):
        return True
    return any(kw in text for kw in HEADER_KEYWORDS)


def _first_single_word(rows: list[list[str]], header_check) -> str:
    """Find the first single-word English entry (not a phrase) to infer section."""
    for row in rows:
        if header_check(row):
            continue
        if not row or not row[0].strip() or row[0].strip() in ("-", "–"):
            continue
        word = row[0].strip()
        # Only use single words (no spaces) — multi-word phrases like "is calling" mislead
        if " " not in word and re.match(r'^[a-zA-Z]', word):
            return word
    return ""


def parse_page_html(html: str, page_number: int, screenshots_dir: Optional[Path] = None,
                    fallback_letter: str = "") -> list[DictionaryEntry]:
    """Parse a single page's HTML into dictionary entries."""
    parser = TableParser()
    parser.feed(html)

    # Check for explicit section header like (A), (B)
    explicit_section = None
    match = re.search(r'\(([A-Z])\)', html)
    if match:
        explicit_section = match.group(1)

    # Fallback: infer from first single-word English entry
    if not explicit_section:
        all_rows = [row for table in parser.tables for row in table]
        first_word = _first_single_word(all_rows, is_header_row)
        if first_word:
            inferred = first_word[0].upper()
            # Only accept if it's a forward transition from the fallback
            if not fallback_letter or inferred >= fallback_letter:
                explicit_section = inferred

    letter = explicit_section or fallback_letter

    entries = []
    screenshot_path = None
    if screenshots_dir:
        img = screenshots_dir / f"page_{page_number:04d}.png"
        if img.exists():
            screenshot_path = f"screenshots/page_{page_number:04d}.png"

    for table in parser.tables:
        for row in table:
            if is_header_row(row):
                continue

            # The table has 6 columns: English, K-D, F-M, Arabic, Kenzi script, Fadija script
            # But sometimes columns are missing or merged
            if len(row) < 3:
                continue

            entry = DictionaryEntry(
                letter_section=letter,
                page_number=page_number,
                source_page_image=screenshot_path,
            )

            # English + romanization: split on comma
            # Arabic + Nubian scripts: split on hyphen
            if len(row) >= 1:
                entry.english = split_comma(row[0])
            if len(row) >= 2:
                entry.kenzi_dongolawi_roman = split_comma(row[1])
            if len(row) >= 3:
                entry.fadija_mahas_roman = split_comma(row[2])
            if len(row) >= 4:
                entry.arabic = split_hyphen(row[3])
            if len(row) >= 5:
                entry.kenzi_dongolawi_script = split_hyphen(row[4])
            if len(row) >= 6:
                entry.fadija_script = split_hyphen(row[5])

            # Skip rows where fewer than 2 columns have real content
            filled = sum(1 for v in [entry.english, entry.kenzi_dongolawi_roman,
                                     entry.fadija_mahas_roman, entry.arabic] if v)
            if filled < 2:
                continue

            # Skip page number rows (just a number)
            if len(entry.english) == 1 and re.match(r'^\d+$', entry.english[0]):
                continue

            entries.append(entry)

    return entries


# ─────────────────────────────────────────────────────────────
# Stage 4: Post-processing
# ─────────────────────────────────────────────────────────────

# Arabic Unicode range (base letters, no diacritics)
_ARABIC_LETTER = re.compile(r'[\u0621-\u064A]')
# Nubian-specific diacritic patterns: Arabic letters with heavy tashkeel
_HEAVY_TASHKEEL = re.compile(r'[\u064B-\u0652]')


def _has_arabic_letters(text: str) -> bool:
    return bool(_ARABIC_LETTER.search(text))


def _tashkeel_density(text: str) -> float:
    """Ratio of diacritics to base letters — Nubian script columns have higher density."""
    letters = len(_ARABIC_LETTER.findall(text))
    diacritics = len(_HEAVY_TASHKEEL.findall(text))
    return diacritics / max(letters, 1)


def detect_contamination(entry: DictionaryEntry) -> list[str]:
    """Flag entries where Arabic text leaked into Nubian script columns or vice versa."""
    warnings = []
    arabic_text = " ".join(entry.arabic)
    kenzi_text = " ".join(entry.kenzi_dongolawi_script)
    fadija_text = " ".join(entry.fadija_script)

    # Arabic column is suspiciously long AND script columns are empty
    if len(arabic_text) > 30 and not kenzi_text and not fadija_text:
        warnings.append("column_bleed: arabic column likely contains merged kenzi/fadija text")

    # Arabic has 3+ hyphen-separated parts AND script columns are empty — strong bleed signal
    arabic_parts = len(entry.arabic)
    if arabic_parts >= 3 and not kenzi_text and not fadija_text:
        warnings.append(f"column_bleed: arabic has {arabic_parts} parts with empty script columns")

    # Script columns contain text that looks like plain Arabic (low diacritic density)
    # Nubian script typically has dense tashkeel; plain Arabic translation text doesn't
    for label, text in [("kenzi", kenzi_text), ("fadija", fadija_text)]:
        if text and _has_arabic_letters(text) and len(text) > 15:
            density = _tashkeel_density(text)
            if density < 0.1:
                warnings.append(f"possible_contamination: {label} text has low diacritic density ({density:.2f})")

    return warnings


def merge_continuation_rows(entries: list[DictionaryEntry]) -> list[DictionaryEntry]:
    """Merge continuation rows into their parent entry.

    Continuation rows have:
    - English starting with '(' (e.g., "(earthernware)")
    - English is '-' but other columns have content
    - Only 1-2 filled columns (the rest are empty)
    """
    if not entries:
        return entries

    merged = []
    for entry in entries:
        eng = entry.english
        is_continuation = False

        # Parenthetical continuation: "(earthernware)", "(ants)"
        if len(eng) == 1 and eng[0].startswith("(") and merged:
            is_continuation = True

        # Dash in english with content in other columns = sub-form of previous entry
        if len(eng) == 1 and eng[0] == "-" and merged:
            filled = sum(1 for v in [entry.kenzi_dongolawi_roman, entry.fadija_mahas_roman,
                                     entry.arabic, entry.kenzi_dongolawi_script, entry.fadija_script] if v)
            if filled <= 3:
                is_continuation = True

        if is_continuation:
            parent = merged[-1]
            # Append non-empty fields to parent
            for field in ["english", "kenzi_dongolawi_roman", "fadija_mahas_roman",
                          "arabic", "kenzi_dongolawi_script", "fadija_script"]:
                parent_val = getattr(parent, field)
                child_val = getattr(entry, field)
                if child_val:
                    setattr(parent, field, parent_val + child_val)
        else:
            merged.append(entry)

    return merged


def postprocess(entries: list[DictionaryEntry]) -> tuple[list[DictionaryEntry], list[dict]]:
    """Run all post-processing steps. Returns (cleaned entries, quality warnings)."""
    entries = merge_continuation_rows(entries)

    warnings = []
    for entry in entries:
        issues = detect_contamination(entry)
        if issues:
            warnings.append({
                "english": entry.english,
                "page": entry.page_number,
                "issues": issues,
            })

    return entries, warnings


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Full pipeline for the Sambaj Nubian Dictionary"
    )
    parser.add_argument("--input", "-i", required=True, help="Path to the PDF")
    parser.add_argument("--output", "-o", required=True, help="Output directory")
    parser.add_argument(
        "--page-range", type=str, default="15-115",
        help="Dictionary page range, 1-indexed (default: 15-115)",
    )
    parser.add_argument("--mode", choices=["fast", "balanced", "accurate"], default="accurate")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--skip-ocr", action="store_true", help="Skip OCR, parse existing HTML")
    parser.add_argument("--skip-screenshots", action="store_true", help="Skip screenshot extraction")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    pdf_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not pdf_path.exists():
        print(f"Error: {pdf_path} not found")
        sys.exit(1)

    parts = args.page_range.split("-")
    page_start = int(parts[0])
    page_end = int(parts[1]) if len(parts) > 1 else page_start
    total_pages = page_end - page_start + 1

    print(f"Sambaj Nubian Dictionary Pipeline")
    print(f"  PDF: {pdf_path}")
    print(f"  Pages: {page_start}-{page_end} ({total_pages} pages)")
    print()

    # Stage 1: Screenshots
    screenshots_dir = None
    if not args.skip_screenshots:
        print("Stage 1: Extracting page screenshots...")
        screenshots_dir = extract_screenshots(pdf_path, output_dir, page_start, page_end)
    else:
        screenshots_dir = output_dir / "screenshots"
        print("Stage 1: Skipped (--skip-screenshots)")
    print()

    # Stage 2: OCR
    if not args.skip_ocr:
        print("Stage 2: OCR via Datalab API...")
        ocr_pages(pdf_path, output_dir, page_start, page_end, args.mode, args.batch_size)
    else:
        print("Stage 2: Skipped (--skip-ocr)")
    print()

    # Stage 3: Parse
    print("Stage 3: Parsing HTML tables...")
    html_dir = output_dir / "html"
    html_files = sorted(html_dir.glob("page_*.html"))

    if not html_files:
        print(f"  No HTML files found in {html_dir}")
        sys.exit(1)

    all_entries = []
    current_letter = ""

    for html_file in html_files:
        page_num = int(html_file.stem.replace("page_", ""))
        if page_num < page_start or page_num > page_end:
            continue

        html = html_file.read_text(encoding="utf-8")
        entries = parse_page_html(html, page_num, screenshots_dir, current_letter)

        # Update current_letter from what the parser detected
        if entries:
            page_letter = entries[0].letter_section
            if page_letter:
                current_letter = page_letter

        # Backfill entries that have no section (e.g., page 15 before any header)
        for e in entries:
            if not e.letter_section and current_letter:
                e.letter_section = current_letter

        all_entries.extend(entries)
        if entries:
            print(f"  page_{page_num:04d}.html: {len(entries)} entries (section {current_letter or '?'})")

    # Stage 4: Post-process
    print(f"\nStage 4: Post-processing ({len(all_entries)} raw entries)...")
    all_entries, quality_warnings = postprocess(all_entries)
    print(f"  After merge: {len(all_entries)} entries")
    if quality_warnings:
        print(f"  Quality warnings: {len(quality_warnings)}")
        for w in quality_warnings:
            print(f"    p{w['page']} {w['english']}: {w['issues']}")

    # Build output
    output_data = {
        "metadata": {**METADATA, "total_entries": len(all_entries)},
        "entries": [e.to_dict() for e in all_entries],
    }
    if quality_warnings:
        output_data["quality_warnings"] = quality_warnings

    output_path = output_dir / "sambaj_dictionary.json"
    indent = 2 if args.pretty else None
    output_path.write_text(
        json.dumps(output_data, ensure_ascii=False, indent=indent),
        encoding="utf-8",
    )

    # Stats
    print(f"\n{'='*60}")
    print(f"Pipeline complete!")
    print(f"  Total entries: {len(all_entries)}")
    sections = {}
    for e in all_entries:
        sections[e.letter_section] = sections.get(e.letter_section, 0) + 1
    print(f"  Sections: {dict(sorted(sections.items()))}")
    if quality_warnings:
        print(f"  Quality warnings: {len(quality_warnings)} (see JSON)")
    print(f"  Output: {output_path}")
    if screenshots_dir and screenshots_dir.exists():
        n_screenshots = len(list(screenshots_dir.glob("*.png")))
        print(f"  Screenshots: {n_screenshots} pages in {screenshots_dir}")


if __name__ == "__main__":
    main()
