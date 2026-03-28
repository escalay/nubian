#!/usr/bin/env python3
"""
Pipeline for Murray 1923 — An English-Nubian Comparative Dictionary.

Scanned PDF from Bayerische Staatsbibliothek. Dense dictionary entries with:
  - Headword + dialect markers (K, M, D, KD, KDM, Dai, Mid, Kdr)
  - Part of speech (s., v.t., v.i., adj., adv., conj., pron., interj., postpos.)
  - English definitions with usage examples
  - Comparative cognates: AR. (Arabic), HAM. (Hamitic), SEM. (Semitic), NIL. (Nilotic), etc.

The OCR renders entries as <ol><li> items — each <li> is one headword.

Usage:
    python murray_pipeline.py \
      -i "../books/murray1923.pdf" \
      -o ../output/murray

    # Test run
    python murray_pipeline.py \
      -i "../books/murray1923.pdf" \
      -o ../output/murray \
      --page-range 49-55
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
from unified_schema import NubianEntry, DialectForm, Cognate, SourceRef, save_entries


# ─────────────────────────────────────────────────────────────
# Screenshots
# ─────────────────────────────────────────────────────────────

def extract_screenshots(pdf_path: Path, output_dir: Path, page_start: int, page_end: int):
    import pypdfium2 as pdfium
    ss_dir = output_dir / "screenshots"
    ss_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    extracted = 0
    for p in range(page_start - 1, min(page_end, len(pdf))):
        out = ss_dir / f"page_{p+1:04d}.png"
        if out.exists(): continue
        bitmap = pdf[p].render(scale=2)
        bitmap.to_pil().save(str(out), "PNG")
        extracted += 1
    print(f"  Screenshots: {extracted} new")
    return ss_dir


# ─────────────────────────────────────────────────────────────
# OCR
# ─────────────────────────────────────────────────────────────

def ocr_pages(pdf_path: Path, output_dir: Path, page_start: int, page_end: int,
              batch_size: int = 10):
    load_dotenv("local.env")
    api_key = os.environ.get("DATALAB_API_KEY")
    if not api_key: print("Error: DATALAB_API_KEY not set"); sys.exit(1)
    os.environ["DATALAB_API_KEY"] = api_key

    from datalab_sdk import DatalabClient, ConvertOptions
    client = DatalabClient()

    html_dir = output_dir / "html"
    html_dir.mkdir(parents=True, exist_ok=True)

    for bs in range(page_start, page_end + 1, batch_size):
        be = min(bs + batch_size - 1, page_end)

        if all((html_dir / f"page_{p:04d}.html").exists() for p in range(bs, be + 1)):
            print(f"  Pages {bs}-{be} — cached")
            continue

        print(f"  Pages {bs}-{be}...", end=" ", flush=True)
        t0 = time.time()

        options = ConvertOptions(
            output_format="html", mode="accurate",
            page_range=f"{bs-1}-{be-1}", paginate=True,
            disable_image_extraction=True,
        )
        result = client.convert(str(pdf_path), options=options)
        html = result.html or ""

        pages = {}
        for m in re.finditer(
            r'<div\s+class="page"\s+data-page-id="(\d+)">(.*?)</div>\s*(?=<div\s+class="page"|</body>)',
            html, re.DOTALL
        ):
            pages[int(m.group(1))] = m.group(2).strip()

        if not pages and html.strip():
            pages[bs - 1] = html

        for idx, api_id in enumerate(sorted(pages.keys())):
            actual = bs + idx
            (html_dir / f"page_{actual:04d}.html").write_text(pages[api_id], encoding="utf-8")

        print(f"OK ({time.time()-t0:.1f}s, {len(pages)} pages)")

    return html_dir


# ─────────────────────────────────────────────────────────────
# Entry Parser
# ─────────────────────────────────────────────────────────────

class LiExtractor(HTMLParser):
    """Extract <li> items from <ol> lists — each is one dictionary entry."""
    def __init__(self):
        super().__init__()
        self.items = []  # list of raw HTML strings per <li>
        self._in_li = False
        self._depth = 0
        self._current = ""

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self._in_li = True
            self._depth += 1
            self._current = ""
        elif self._in_li:
            attr_str = " ".join(f'{k}="{v}"' for k, v in attrs)
            self._current += f"<{tag} {attr_str}>" if attr_str else f"<{tag}>"

    def handle_endtag(self, tag):
        if tag == "li" and self._in_li:
            self._depth -= 1
            if self._depth <= 0:
                self._in_li = False
                self.items.append(self._current.strip())
        elif self._in_li:
            self._current += f"</{tag}>"

    def handle_data(self, data):
        if self._in_li:
            self._current += data


# Dialect codes used by Murray
DIALECT_CODES = {
    "K.": "K", "M.": "M", "D.": "D",
    "KD.": "KD", "KM.": "KM", "DM.": "DM",
    "KDM.": "KDM", "Dai.": "Dai", "Mid.": "Mid",
    "Kdr.": "Kdr", "M.Mid.": "M",
}

# POS abbreviations
POS_TAGS = {"s.", "v.t.", "v.i.", "adj.", "adv.", "conj.", "pron.", "interj.",
            "postpos.", "participle", "prep."}

# Comparative language markers
COGNATE_MARKERS = {
    "AR.": "Arabic", "ar.": "Arabic",
    "HAM.": "Hamitic", "ham.": "Hamitic",
    "SEM.": "Semitic", "sem.": "Semitic",
    "NIL.": "Nilotic", "nil.": "Nilotic",
    "CENT.": "Central African",
    "EG.": "Egyptian",
    "SUD.": "Sudanese Arabic",
    "Cf.": "compare",
    "cf.": "compare",
}


def strip_tags(html: str) -> str:
    """Remove HTML tags, keep text."""
    return re.sub(r'<[^>]+>', '', html).strip()


def extract_headword(li_html: str) -> tuple[str, str]:
    """Extract headword (first text before any <i> tag) and the rest of the entry."""
    # The headword is the text before the first italic tag
    match = re.match(r'^(.*?)<i', li_html, re.DOTALL)
    if match:
        hw = strip_tags(match.group(1)).strip().rstrip('.')
        rest = li_html[match.start():]
        return hw, rest

    # No italic — whole thing is the entry
    return strip_tags(li_html).split('.')[0].strip(), li_html


def extract_dialects(text: str) -> list[str]:
    """Find dialect markers like K., M., KDM., etc."""
    dialects = []
    for code, dialect in DIALECT_CODES.items():
        if code in text:
            dialects.append(dialect)
    return list(set(dialects))


def extract_pos(text: str) -> str:
    """Extract part of speech."""
    for pos in sorted(POS_TAGS, key=len, reverse=True):
        if pos in text:
            return pos.rstrip('.')
    return ""


def extract_cognates(text: str) -> list[Cognate]:
    """Extract comparative cognates from the right column."""
    cognates = []
    for marker, language in COGNATE_MARKERS.items():
        pattern = re.compile(re.escape(marker) + r'\s*([^.;]+(?:\.[^.;]*)?)')
        for m in pattern.finditer(text):
            form = m.group(1).strip().rstrip('.')
            if form and len(form) > 1:
                cognates.append(Cognate(language=language, form=form))
    return cognates


def parse_entry(li_html: str, page_number: int, entry_idx: int) -> Optional[NubianEntry]:
    """Parse a single <li> entry into a NubianEntry."""
    headword, rest = extract_headword(li_html)

    if not headword or len(headword) > 80:
        return None

    # Skip page headers like "abā—abi-n" or "a—aba"
    if re.match(r'^[a-zā-ž].*—[a-zā-ž]', headword):
        return None

    full_text = strip_tags(li_html)

    dialects = extract_dialects(full_text)
    pos = extract_pos(full_text)
    cognates = extract_cognates(full_text)

    # Extract definition: text after POS and dialect, before cognate markers
    definition = ""
    # Remove headword, dialect codes, POS from the text to get definition
    def_text = full_text
    for code in DIALECT_CODES:
        def_text = def_text.replace(code, "")
    for p in POS_TAGS:
        def_text = def_text.replace(p, "", 1)
    # Remove cognate sections (AR., HAM., etc.)
    for marker in COGNATE_MARKERS:
        idx = def_text.find(marker)
        if idx > 0:
            def_text = def_text[:idx]
    def_text = def_text.replace(headword, "", 1).strip().lstrip('.').strip()
    if def_text:
        definition = re.sub(r'\s+', ' ', def_text).strip()

    entry = NubianEntry(
        id=f"murray_{page_number}_{entry_idx}",
        entry_type="word",
        headword=headword,
        part_of_speech=pos,
        english=[definition] if definition else [],
        cognates=cognates,
        sources=[SourceRef(
            book="murray",
            page=page_number,
            screenshot=f"screenshots/page_{page_number:04d}.png",
        )],
    )

    # Add dialect forms
    for d in dialects:
        entry.forms.append(DialectForm(dialect=d, romanization=headword))

    # Determine letter section
    clean = re.sub(r'^[^a-zA-Z]*', '', headword)
    if clean:
        entry.letter_section = clean[0].upper()

    return entry


def parse_page(html: str, page_number: int) -> list[NubianEntry]:
    """Parse all entries from a page's HTML."""
    parser = LiExtractor()
    parser.feed(html)

    entries = []
    for idx, li_html in enumerate(parser.items):
        entry = parse_entry(li_html, page_number, idx)
        if entry:
            entries.append(entry)

    return entries


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Murray 1923 Dictionary Pipeline")
    parser.add_argument("--input", "-i", required=True)
    parser.add_argument("--output", "-o", required=True)
    parser.add_argument("--page-range", default="49-237")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--skip-screenshots", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    pdf_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    parts = args.page_range.split("-")
    page_start, page_end = int(parts[0]), int(parts[1])

    print(f"Murray 1923 English-Nubian Comparative Dictionary")
    print(f"  PDF: {pdf_path}")
    print(f"  Pages: {page_start}-{page_end} ({page_end - page_start + 1} pages)")
    print()

    # Stage 1: Screenshots
    if not args.skip_screenshots:
        print("Stage 1: Extracting screenshots...")
        extract_screenshots(pdf_path, output_dir, page_start, page_end)
    print()

    # Stage 2: OCR
    if not args.skip_ocr:
        print("Stage 2: OCR via Datalab API (accurate mode)...")
        ocr_pages(pdf_path, output_dir, page_start, page_end, args.batch_size)
    print()

    # Stage 3: Parse
    print("Stage 3: Parsing dictionary entries...")
    html_dir = output_dir / "html"
    all_entries = []

    for p in range(page_start, page_end + 1):
        html_file = html_dir / f"page_{p:04d}.html"
        if not html_file.exists():
            continue
        html = html_file.read_text(encoding="utf-8")
        entries = parse_page(html, p)
        all_entries.extend(entries)
        if entries:
            sections = set(e.letter_section for e in entries if e.letter_section)
            print(f"  page_{p:04d}.html: {len(entries)} entries ({', '.join(sorted(sections))})")

    # Clean newlines
    for e in all_entries:
        e.english = [re.sub(r'\s+', ' ', d).strip() for d in e.english]

    # Save
    print(f"\nStage 4: Saving {len(all_entries)} entries...")
    output_path = str(output_dir / "murray_parsed.json")
    save_entries(all_entries, output_path, "murray")

    # Stats
    from collections import Counter
    sections = Counter(e.letter_section for e in all_entries)
    pos_counts = Counter(e.part_of_speech for e in all_entries if e.part_of_speech)
    with_cognates = sum(1 for e in all_entries if e.cognates)
    with_def = sum(1 for e in all_entries if e.english)
    dialect_counts = Counter(d.dialect for e in all_entries for d in e.forms)

    print(f"\n  Sections: {dict(sorted(sections.items()))}")
    print(f"  POS: {dict(pos_counts.most_common(10))}")
    print(f"  Dialects: {dict(dialect_counts.most_common(10))}")
    print(f"  With definitions: {with_def}")
    print(f"  With cognates: {with_cognates}")


if __name__ == "__main__":
    main()
