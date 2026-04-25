#!/usr/bin/env python3
"""
Armbruster Lexicon Pipeline — Column-based OCR + Deterministic Parser.

Architecture:
  Stage 1: Column splitting (already done — 615 images in output/armbruster/columns/)
  Stage 2: OCR each column via Datalab API → HTML per column
  Stage 3: Deterministic parser → structured JSON entries
  Stage 4: (Optional) LLM enrichment pass for Arabic/IPA/categories

Each stage outputs to its own folder for traceability:
  output/armbruster/
    columns/           ← Stage 1: page_XXXX_col{1,2,3}.png
    ocr-columns/       ← Stage 2: page_XXXX_col{1,2,3}.html
    parsed/            ← Stage 3: page_XXXX.json + armbruster_parsed.json
    screenshots/       ← Full page PNGs for reference

Usage:
    # OCR all columns
    python armbruster_pipeline.py ocr --page-range 18-222

    # Parse all OCR'd columns
    python armbruster_pipeline.py parse --page-range 18-222

    # Full pipeline
    python armbruster_pipeline.py all --page-range 18-30
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


# ─────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent / "output" / "armbruster"
COLUMNS_DIR = BASE_DIR / "columns"
OCR_DIR = BASE_DIR / "ocr-columns"
PARSED_DIR = BASE_DIR / "parsed"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"

PDF_PATH = Path(__file__).parent.parent / "books" / "Dongolese Nubian_ A Lexicon - Charles Hubert Armbruster (1).pdf"


# ─────────────────────────────────────────────────────────────
# Stage 2: OCR columns via Datalab API
# ─────────────────────────────────────────────────────────────

def ocr_columns(page_start: int, page_end: int, batch_size: int = 5):
    """OCR each column image individually via Datalab API."""
    load_dotenv("local.env")
    api_key = os.environ.get("DATALAB_API_KEY")
    if not api_key:
        print("Error: DATALAB_API_KEY not set"); sys.exit(1)
    os.environ["DATALAB_API_KEY"] = api_key

    from datalab_sdk import DatalabClient, ConvertOptions
    client = DatalabClient()

    OCR_DIR.mkdir(parents=True, exist_ok=True)

    total = 0
    skipped = 0
    failed = 0
    failed_columns = []

    for page in range(page_start, page_end + 1):
        for col in [1, 2, 3]:
            col_img = COLUMNS_DIR / f"page_{page:04d}_col{col}.png"
            out_html = OCR_DIR / f"page_{page:04d}_col{col}.html"

            if not col_img.exists():
                continue

            if out_html.exists():
                skipped += 1
                continue

            print(f"  p{page} col{col}...", end=" ", flush=True)
            t0 = time.time()

            try:
                options = ConvertOptions(
                    output_format="html",
                    mode="accurate",
                    disable_image_extraction=True,
                )
                result = client.convert(str(col_img), options=options)
                html = result.html or ""

                # Strip the HTML wrapper, keep just the body content
                body_match = re.search(r'<body>(.*?)</body>', html, re.DOTALL)
                content = body_match.group(1).strip() if body_match else html

                out_html.write_text(content, encoding="utf-8")
                total += 1
                dt = time.time() - t0
                print(f"OK ({dt:.1f}s, {len(content)} chars)")
            except Exception as e:
                failed += 1
                failed_columns.append(f"p{page} col{col}")
                print(f"ERROR: {e}")

    print(f"\n  OCR complete: {total} new, {skipped} cached, {failed} failed")
    if failed:
        print(f"  Failed columns: {', '.join(failed_columns[:20])}")
        if len(failed_columns) > 20:
            print(f"  ...and {len(failed_columns) - 20} more")
        sys.exit(1)


# ─────────────────────────────────────────────────────────────
# Stage 3: Deterministic Parser
# ─────────────────────────────────────────────────────────────

# The ˘ connector appears as <sub>⌣</sub> in the OCR output
BREVE_PATTERN = re.compile(r'<sub>\s*⌣\s*</sub>')

# Bold = headword
BOLD_PATTERN = re.compile(r'<b>(.*?)</b>', re.DOTALL)

# Italic = English definition
ITALIC_PATTERN = re.compile(r'<i>(.*?)</i>', re.DOTALL)

# Grammar references
GRAMMAR_REF = re.compile(r'§§?\d+[a-z]?(?:[-–]\d+[a-z]?)?(?:ff\.?)?')

# POS tags (in order of length for greedy match)
POS_TAGS = sorted([
    "v.t.", "v.i.", "v.p.", "n.", "adj.", "adv.", "stat.", "caus.",
    "prep.", "conj.", "interj.", "pron.", "num.", "postp.", "postpos.",
    "n. act.", "n. dic.", "dem.", "app.", "dim.", "coll.", "freq.",
    "apocritic sentence-word",
], key=len, reverse=True)

# Etymology pattern
ETYMOLOGY_PATTERN = re.compile(r'\[([^\]]*(?:\[[^\]]*\])*[^\]]*)\]')


def normalize_breve(html: str) -> str:
    """Replace <sub>⌣</sub> with ˘ and collapse surrounding whitespace.

    The OCR outputs:
        word
        <sub>
         ⌣
        </sub>
        nextword

    We want: word˘nextword
    """
    # First replace the tag pattern
    result = BREVE_PATTERN.sub('˘', html)
    # Collapse whitespace around ˘: "word \n ˘ \n next" → "word˘next"
    result = re.sub(r'\s*˘\s*', '˘', result)
    return result


def strip_tags(html: str) -> str:
    """Remove all HTML tags, keep text."""
    return re.sub(r'<[^>]+>', '', html).strip()


def extract_entries_from_column(html: str, page_number: int, col_number: int) -> list[dict]:
    """Parse a single column's OCR HTML into structured entries."""

    # Step 1: Normalize ˘
    html = normalize_breve(html)

    # Step 2: Split into <p> blocks — each may be an entry or continuation
    p_blocks = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)

    entries = []
    current_entry = None

    for block in p_blocks:
        block = block.strip()
        if not block:
            continue

        # Check if this block starts with a bold headword
        bold_match = re.match(r'^\s*<b>(.*?)</b>', block, re.DOTALL)

        if bold_match:
            headword_raw = strip_tags(bold_match.group(1)).strip()

            # Skip page headers like "áb — áb-bê-"
            if '—' in headword_raw and len(headword_raw) > 10:
                continue
            # Skip section headers
            if re.match(r'^[A-Z]$', headword_raw):
                continue

            # Save previous entry
            if current_entry and current_entry.get('headword'):
                entries.append(current_entry)

            # Start new entry
            current_entry = {
                'headword': headword_raw,
                'headword_normalized': re.sub(r'[˘\-]', '', headword_raw).lower()
                    .replace('á', 'a').replace('é', 'e').replace('í', 'i')
                    .replace('ó', 'o').replace('ú', 'u').replace('ā', 'aa')
                    .replace('ē', 'ee').replace('ī', 'ii').replace('ō', 'oo')
                    .replace('ū', 'uu').replace('ñ', 'ny').replace('š', 'sh')
                    .replace('č', 'ch'),
                'pos': '',
                'grammar_refs': [],
                'english': [],
                'object_form': '',
                'plural_form': '',
                'genitive_form': '',
                'verb_forms': {},
                'usage_examples': [],
                'etymology': '',
                'page_number': page_number,
                'column': col_number,
                'source_screenshot': f"screenshots/page_{page_number:04d}.png",
                'raw_html': block,
            }

            # Extract from the rest of the block (after headword)
            rest = block[bold_match.end():]
            _parse_entry_content(current_entry, rest)

        elif current_entry:
            # Continuation block — add to current entry
            _parse_continuation(current_entry, block)

    # Don't forget the last entry
    if current_entry and current_entry.get('headword'):
        entries.append(current_entry)

    return entries


def _parse_entry_content(entry: dict, html: str):
    """Parse the main content after the headword in an entry block."""
    text = strip_tags(html).strip()

    # Extract grammar refs
    refs = GRAMMAR_REF.findall(text)
    if refs:
        entry['grammar_refs'] = refs

    # Extract POS
    for pos in POS_TAGS:
        if pos in text:
            entry['pos'] = pos.rstrip('.')
            break

    # Extract italic text = English definitions
    italics = ITALIC_PATTERN.findall(html)
    for it in italics:
        clean = strip_tags(it).strip().rstrip('.')
        if clean and len(clean) > 1:
            entry['english'].append(clean)

    # Fallback: if no italic definitions found but POS exists,
    # extract English from text after POS tag
    if not entry['english'] and entry['pos']:
        pos_idx = text.find(entry['pos'] + '.')
        if pos_idx >= 0:
            after_pos = text[pos_idx + len(entry['pos']) + 1:].strip()
            # Remove grammar refs
            after_pos = GRAMMAR_REF.sub('', after_pos).strip()
            # Remove etymology
            after_pos = ETYMOLOGY_PATTERN.sub('', after_pos).strip()
            # Take text before any — dash (compound forms)
            if '—' in after_pos:
                after_pos = after_pos.split('—')[0].strip()
            # Take text before "s.v." cross-reference
            if 's.v.' in after_pos:
                after_pos = after_pos.split('s.v.')[0].strip()
            after_pos = after_pos.strip('.,;: ')
            if after_pos and len(after_pos) > 2:
                entry['english'].append(after_pos)

    # Extract etymology
    ety_match = ETYMOLOGY_PATTERN.search(text)
    if ety_match:
        entry['etymology'] = ety_match.group(0)


def _parse_continuation(entry: dict, html: str):
    """Parse a continuation block (no bold headword)."""
    text = strip_tags(html).strip()

    # Object form
    if text.startswith('obj.'):
        entry['object_form'] = text[4:].strip().rstrip('.')
        return

    # Plural form
    if text.startswith('pl.'):
        entry['plural_form'] = text[3:].strip().rstrip('.')
        return

    # Genitive form
    if text.startswith('gen.'):
        entry['genitive_form'] = text[4:].strip().rstrip('.')
        return

    # Verb forms
    for label in ['pres.', 'perf.', 'imperat.', 'part. pres.', 'part. past.', 'ind. pres.']:
        if text.startswith(label):
            key = label.rstrip('.').replace(' ', '_').replace('.', '')
            entry['verb_forms'][key] = text[len(label):].strip().rstrip('.')
            return

    # Subjunctive/past
    if text.startswith('subj.'):
        entry['verb_forms']['subjunctive'] = text[5:].strip().rstrip('.')
        return

    # Check for italic = additional English definitions
    italics = ITALIC_PATTERN.findall(html)
    if italics:
        for it in italics:
            clean = strip_tags(it).strip().rstrip('.')
            if clean and len(clean) > 1:
                entry['english'].append(clean)
        return

    # Check for usage example patterns:
    # 1. "nubian˘text italic English translation" (with <i> tags)
    if '<i>' in html:
        nubian_part = strip_tags(html.split('<i>')[0]).strip()
        english_parts = ITALIC_PATTERN.findall(html)
        if nubian_part and english_parts and len(nubian_part) > 3:
            eng = strip_tags(english_parts[0]).strip()
            # Only count as example if the nubian part has ˘ or diacritics
            if '˘' in nubian_part or re.search(r'[áéíóúāēīōū]', nubian_part):
                entry['usage_examples'].append({
                    'nubian': nubian_part,
                    'english': eng,
                })
                return
        # If italic text is a definition, add to english
        for it in english_parts:
            clean = strip_tags(it).strip().rstrip('.')
            if clean and len(clean) > 1:
                entry['english'].append(clean)
        return

    # 2. "nubian˘text English sentence." (no italic — common in Armbruster)
    # Pattern: starts with lowercase Nubian word with ˘, then English
    if '˘' in text and len(text) > 10:
        # Try to split at the first English-looking phrase
        # Heuristic: English starts with (s)he, the, a, I, he, she, it, they, we, you, my, his
        eng_match = re.search(r'\b(\(s\)he|the |a |I |he |she |it |they |we |you |my |his |her |one |this |that |is |are |was |not |will |shall |let |if |when |after )', text)
        if eng_match:
            nubian_part = text[:eng_match.start()].strip()
            english_part = text[eng_match.start():].strip()
            if nubian_part and english_part:
                entry['usage_examples'].append({
                    'nubian': nubian_part,
                    'english': english_part,
                })
                return

    # Grammar refs in continuation
    refs = GRAMMAR_REF.findall(text)
    if refs:
        entry['grammar_refs'].extend(refs)

    # Etymology
    ety_match = ETYMOLOGY_PATTERN.search(text)
    if ety_match:
        entry['etymology'] = ety_match.group(0)


def parse_page(page_number: int) -> list[dict]:
    """Parse all 3 columns for a page and merge."""
    all_entries = []

    for col in [1, 2, 3]:
        html_path = OCR_DIR / f"page_{page_number:04d}_col{col}.html"
        if not html_path.exists():
            continue

        html = html_path.read_text(encoding="utf-8")
        entries = extract_entries_from_column(html, page_number, col)
        all_entries.extend(entries)

    return all_entries


def run_parse(page_start: int, page_end: int):
    """Parse all OCR'd columns into structured entries."""
    PARSED_DIR.mkdir(parents=True, exist_ok=True)

    all_entries = []

    for page in range(page_start, page_end + 1):
        entries = parse_page(page)
        if not entries:
            continue

        # Save per-page
        page_out = PARSED_DIR / f"page_{page:04d}.json"
        page_data = {
            'page_number': page,
            'total_entries': len(entries),
            'entries': entries,
        }
        page_out.write_text(json.dumps(page_data, ensure_ascii=False, indent=2), encoding="utf-8")
        all_entries.extend(entries)

        if entries:
            print(f"  page_{page:04d}: {len(entries)} entries")

    # Save combined
    combined = {
        'metadata': {
            'source': 'Armbruster, C.H. (1965). Dongolese Nubian: A Lexicon.',
            'method': 'Column OCR (Datalab API) + deterministic parser',
            'dialect': 'Dongolawi',
            'total_entries': len(all_entries),
            'breve_symbol': '˘ detected as <sub>⌣</sub> in OCR, normalized to ˘',
        },
        'entries': all_entries,
    }

    out_path = PARSED_DIR / "armbruster_parsed.json"
    out_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")

    # Stats
    has_breve = sum(1 for e in all_entries if '˘' in e.get('headword', ''))
    has_english = sum(1 for e in all_entries if e.get('english'))
    has_pos = sum(1 for e in all_entries if e.get('pos'))
    has_refs = sum(1 for e in all_entries if e.get('grammar_refs'))
    has_ety = sum(1 for e in all_entries if e.get('etymology'))
    has_verb = sum(1 for e in all_entries if e.get('verb_forms'))
    has_examples = sum(1 for e in all_entries if e.get('usage_examples'))

    print(f"\n{'='*60}")
    print(f"Parsed {len(all_entries)} entries from pages {page_start}-{page_end}")
    print(f"  Headwords with ˘: {has_breve} ({has_breve/max(len(all_entries),1)*100:.0f}%)")
    print(f"  With English:     {has_english} ({has_english/max(len(all_entries),1)*100:.0f}%)")
    print(f"  With POS:         {has_pos} ({has_pos/max(len(all_entries),1)*100:.0f}%)")
    print(f"  With grammar §:   {has_refs} ({has_refs/max(len(all_entries),1)*100:.0f}%)")
    print(f"  With etymology:   {has_ety} ({has_ety/max(len(all_entries),1)*100:.0f}%)")
    print(f"  With verb forms:  {has_verb} ({has_verb/max(len(all_entries),1)*100:.0f}%)")
    print(f"  With examples:    {has_examples} ({has_examples/max(len(all_entries),1)*100:.0f}%)")
    print(f"  Output: {out_path}")


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Armbruster Lexicon Pipeline")
    parser.add_argument("stage", choices=["ocr", "parse", "all"])
    parser.add_argument("--page-range", default="18-222")
    args = parser.parse_args()

    parts = args.page_range.split("-")
    page_start, page_end = int(parts[0]), int(parts[1])

    print(f"Armbruster Lexicon Pipeline")
    print(f"  Pages: {page_start}-{page_end}")
    print()

    if args.stage in ("ocr", "all"):
        print("Stage 2: OCR columns via Datalab API...")
        ocr_columns(page_start, page_end)
        print()

    if args.stage in ("parse", "all"):
        print("Stage 3: Parsing OCR HTML...")
        run_parse(page_start, page_end)


if __name__ == "__main__":
    main()
