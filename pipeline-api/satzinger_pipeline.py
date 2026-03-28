#!/usr/bin/env python3
"""
Pipeline for the Satzinger Archaeological Wordlist.

Born-digital PDF with clean 3-column tables (English / Sudan Arabic / Nobiin).
Entries are grouped by topic category.

Usage:
    python satzinger_pipeline.py \
      -i "../books/Satzinger-Wordlist-Arabic-Nobiin-2018.pdf" \
      -o ../output/satzinger
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
from unified_schema import NubianEntry, DialectForm, SourceRef, save_entries


# ─────────────────────────────────────────────────────────────
# OCR
# ─────────────────────────────────────────────────────────────

def ocr_pages(pdf_path: Path, output_dir: Path, page_start: int, page_end: int,
              mode: str = "balanced"):
    """OCR via Datalab API. Born-digital so balanced mode is fine."""
    load_dotenv("local.env")
    api_key = os.environ.get("DATALAB_API_KEY")
    if not api_key:
        print("Error: DATALAB_API_KEY not set"); sys.exit(1)
    os.environ["DATALAB_API_KEY"] = api_key

    from datalab_sdk import DatalabClient, ConvertOptions
    client = DatalabClient()

    html_dir = output_dir / "html"
    html_dir.mkdir(parents=True, exist_ok=True)

    api_range = f"{page_start - 1}-{page_end - 1}"

    # Check cache
    all_exist = all(
        (html_dir / f"page_{p:04d}.html").exists()
        for p in range(page_start, page_end + 1)
    )
    if all_exist:
        print(f"  Pages {page_start}-{page_end} — cached")
        return html_dir

    print(f"  Pages {page_start}-{page_end}...", end=" ", flush=True)
    t0 = time.time()

    options = ConvertOptions(
        output_format="html",
        mode=mode,
        page_range=api_range,
        paginate=True,
        disable_image_extraction=True,
    )

    result = client.convert(str(pdf_path), options=options)
    html = result.html or ""

    # Split paginated HTML
    pattern = re.compile(
        r'<div\s+class="page"\s+data-page-id="(\d+)">(.*?)</div>\s*(?=<div\s+class="page"|</body>)',
        re.DOTALL,
    )
    pages = {}
    for match in pattern.finditer(html):
        pages[int(match.group(1))] = match.group(2).strip()

    if not pages and html.strip():
        pages[page_start - 1] = html

    for idx, api_id in enumerate(sorted(pages.keys())):
        actual_page = page_start + idx
        (html_dir / f"page_{actual_page:04d}.html").write_text(
            pages[api_id], encoding="utf-8"
        )

    dt = time.time() - t0
    print(f"OK ({dt:.1f}s, {len(pages)} pages)")
    return html_dir


# ─────────────────────────────────────────────────────────────
# Table Parser
# ─────────────────────────────────────────────────────────────

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self._in_table = self._in_row = self._in_cell = False
        self._row = []; self._cell = ""; self._table = []

    def handle_starttag(self, tag, attrs):
        if tag == "table": self._in_table = True; self._table = []
        elif tag == "tr" and self._in_table: self._in_row = True; self._row = []
        elif tag in ("td", "th") and self._in_row: self._in_cell = True; self._cell = ""
        elif tag == "br" and self._in_cell: self._cell += "; "

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            self._row.append(self._cell.strip())
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._row: self._table.append(self._row)
        elif tag == "table" and self._in_table:
            self._in_table = False
            if self._table: self.tables.append(self._table)

    def handle_data(self, data):
        if self._in_cell: self._cell += data


# Category headers in the book
HEADER_KEYWORDS = {"English", "Sudan Arabic", "Sudan Arabics", "Nobiin", "Mahasi-Nubian"}

# Topic categories from the book
TOPIC_MAP = {
    "Greetings & Salutations": "greeting",
    "Greetings": "greeting",
    "Personal Pronouns": "pronoun",
    "Archaeology": "archaeology",
    "Materials": "material",
    "Colours": "colour",
    "Architecture": "architecture",
    "At the camp": "camp",
    "Kitchen": "kitchen",
    "Food, Meals": "food",
    "Food": "food",
    "Cardinal Points": "direction",
    "Geography": "geography",
    "Right – Left": "direction",
    "Right": "direction",
    "Time Expressions": "time",
    "Time": "time",
}


def detect_category(text: str) -> Optional[str]:
    """Match a table header row to a topic category."""
    for key, cat in TOPIC_MAP.items():
        if key.lower() in text.lower():
            return cat
    return None


def is_header_row(row: list[str]) -> bool:
    text = " ".join(row)
    return any(kw in text for kw in HEADER_KEYWORDS)


def parse_page(html: str, page_number: int, current_category: str = "") -> tuple[list[NubianEntry], str]:
    """Parse a single page. Returns (entries, last_category)."""
    parser = TableParser()
    parser.feed(html)

    entries = []

    for table in parser.tables:
        for row in table:
            # Check for category header (first row of each table)
            row_text = " ".join(row)
            cat = detect_category(row_text)
            if cat:
                current_category = cat
                continue

            if is_header_row(row):
                continue

            if len(row) < 2:
                continue

            english = row[0].strip() if len(row) >= 1 else ""
            sudani = row[1].strip() if len(row) >= 2 else ""
            nobiin = row[2].strip() if len(row) >= 3 else ""

            if not english or english in ("-", "–"):
                continue

            # Handle response markers
            entry_type = "word"
            if english.startswith("► ") or english.startswith("▸ ") or english.startswith("> "):
                english = english.lstrip("►▸> ").strip()
                if "response" in english.lower():
                    english = f"response: {english.replace('response:', '').strip()}"

            # Determine if this is a phrase/greeting
            if " " in english and current_category == "greeting":
                entry_type = "greeting"
            elif " " in english:
                entry_type = "phrase"

            # Build entry
            entry = NubianEntry(
                id=f"satzinger_{page_number}_{len(entries)}",
                entry_type=entry_type,
                headword=english,
                category=current_category,
                english=[english],
                sudani_arabic=[s.strip() for s in sudani.split(";") if s.strip()] if sudani else [],
                sources=[SourceRef(book="satzinger", page=page_number)],
            )

            # Parse Nobiin forms (Mahas-Nubian dialect)
            if nobiin and nobiin not in ("(Same.)", "(Same.)"):
                # Split multiple forms on ; or /
                forms_text = [f.strip() for f in re.split(r'[;]', nobiin) if f.strip()]
                for form_text in forms_text:
                    # Extract parenthetical notes
                    notes = ""
                    note_match = re.search(r'\(([^)]+)\)', form_text)
                    if note_match:
                        notes = note_match.group(1)
                        form_text = form_text[:note_match.start()].strip()

                    # Split singular/plural on /
                    variants = [v.strip() for v in form_text.split("/") if v.strip()]
                    roman = variants[0] if variants else form_text
                    plural = variants[1] if len(variants) > 1 else ""

                    entry.forms.append(DialectForm(
                        dialect="FM",
                        romanization=roman,
                        plural=plural,
                        notes=notes,
                    ))
            elif nobiin == "(Same.)":
                # Same as Arabic — copy the Arabic form
                entry.forms.append(DialectForm(
                    dialect="FM",
                    romanization=sudani.split("/")[0].strip() if sudani else "",
                    notes="same as Arabic",
                ))

            entries.append(entry)

    return entries, current_category


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Satzinger Wordlist Pipeline")
    parser.add_argument("--input", "-i", required=True)
    parser.add_argument("--output", "-o", required=True)
    parser.add_argument("--mode", default="balanced", choices=["fast", "balanced", "accurate"])
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    pdf_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    PAGE_START, PAGE_END = 7, 19

    print(f"Satzinger Wordlist Pipeline")
    print(f"  PDF: {pdf_path}")
    print(f"  Pages: {PAGE_START}-{PAGE_END}")
    print()

    # Stage 1: OCR
    if not args.skip_ocr:
        print("Stage 1: OCR via Datalab API...")
        ocr_pages(pdf_path, output_dir, PAGE_START, PAGE_END, args.mode)
    else:
        print("Stage 1: Skipped")
    print()

    # Stage 2: Parse
    print("Stage 2: Parsing tables...")
    html_dir = output_dir / "html"
    all_entries = []
    current_category = ""

    for p in range(PAGE_START, PAGE_END + 1):
        html_file = html_dir / f"page_{p:04d}.html"
        if not html_file.exists():
            continue
        html = html_file.read_text(encoding="utf-8")
        entries, current_category = parse_page(html, p, current_category)
        all_entries.extend(entries)
        if entries:
            cats = set(e.category for e in entries)
            print(f"  page_{p:04d}.html: {len(entries)} entries ({', '.join(cats)})")

    # Stage 3: Save
    print(f"\nStage 3: Saving {len(all_entries)} entries...")
    output_path = str(output_dir / "satzinger_parsed.json")
    save_entries(all_entries, output_path, "satzinger")

    # Stats
    from collections import Counter
    cats = Counter(e.category for e in all_entries)
    types = Counter(e.entry_type for e in all_entries)
    print(f"\n  Categories: {dict(sorted(cats.items()))}")
    print(f"  Types: {dict(types)}")
    print(f"  Entries with Nobiin: {sum(1 for e in all_entries if e.forms)}")


if __name__ == "__main__":
    main()
