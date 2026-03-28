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
    match = re.match(r'^(.*?)<i', li_html, re.DOTALL)
    if match:
        hw = strip_tags(match.group(1)).strip().rstrip('.')
        rest = li_html[match.start():]
    else:
        hw = strip_tags(li_html).split('.')[0].strip()
        rest = li_html

    # Strip trailing dialect codes from headword (e.g., "deg DM" → "deg")
    hw = re.sub(r'\s+(' + '|'.join(re.escape(c.rstrip('.')) for c in DIALECT_CODES) + r')\.?\s*$', '', hw)
    return hw.strip(), rest


def extract_dialects(text: str) -> list[str]:
    """Find dialect markers like K., M., KDM., etc."""
    dialects = []
    for code, dialect in DIALECT_CODES.items():
        if code in text:
            dialects.append(dialect)
    return list(set(dialects))


def extract_pos(text: str) -> str:
    """Extract part of speech — takes the FIRST occurrence near the start of the entry."""
    # Only look in the first ~80 chars (after headword + dialect, before the definition body)
    early = text[:80]
    best_pos = ""
    best_idx = 999
    for pos in POS_TAGS:
        idx = early.find(pos)
        if idx >= 0 and idx < best_idx:
            best_idx = idx
            best_pos = pos
    return best_pos.rstrip('.') if best_pos else ""


# All markers that signal the start of cognate/comparative text
_COGNATE_BOUNDARY = re.compile(
    r'\b(?:HAM\.|ham\.|AR\.|ar\.|SEM\.|sem\.|NIL\.|nil\.|CENT\.|EG\.|SUD\.|'
    r'Cf\.|cf\.|Copt\.|Bed\.|Som\.|Bil\.|Qu\.|Sa\.|Ga\.|'
    r'Haus\.|Ba\.|Mas\.|Kham\.|Barea|Nan\.|'
    r'HA [A-Z]|SE [A-Z])',  # truncated markers at line breaks
)

# Markers for cross-references: "s. word" meaning "see word"
_XREF = re.compile(r'\bs\.\s+[a-záéíóúāēīōūǎǐǒǔ]')


def extract_cognates(text: str) -> list[Cognate]:
    """Extract comparative cognates from the right-column text."""
    cognates = []
    for marker, language in COGNATE_MARKERS.items():
        if marker in ("Cf.", "cf."):
            # For cf./Cf., check what language follows
            for m in re.finditer(re.escape(marker) + r'\s*(.{3,60})', text):
                remainder = m.group(1).strip()
                # Check if a real language marker follows
                real_lang = None
                for sub_marker, sub_lang in COGNATE_MARKERS.items():
                    if sub_marker in ("Cf.", "cf."):
                        continue
                    if remainder.startswith(sub_marker):
                        real_lang = sub_lang
                        remainder = remainder[len(sub_marker):].strip()
                        break
                form = re.split(r'[.;]', remainder)[0].strip()
                if form and len(form) > 1:
                    cognates.append(Cognate(
                        language=real_lang or "compare",
                        form=form,
                    ))
        else:
            for m in re.finditer(re.escape(marker) + r'\s*([^.;]{2,50})', text):
                form = m.group(1).strip().rstrip('.')
                if form:
                    cognates.append(Cognate(language=language, form=form))
    return cognates


def clean_definition(raw_def: str, headword: str) -> str:
    """Extract clean English meaning from raw definition text.

    The raw text often has: dialect codes, variant Nubian forms, cognate text,
    cross-references, and usage examples all mixed together. We extract just
    the English meaning.
    """
    text = raw_def

    # 1. Remove the headword itself
    text = text.replace(headword, "", 1)

    # 2. Remove all dialect codes
    for code in DIALECT_CODES:
        text = text.replace(code, " ")
    # Also remove bare dialect codes without period (K, M, D, KD, KDM...)
    text = re.sub(r'^[KDM]{1,3}\s+', '', text.strip())

    # 3. Remove POS tags
    for p in POS_TAGS:
        text = text.replace(p, " ", 1)

    # 4. Truncate at cognate boundaries (right-column material)
    m = _COGNATE_BOUNDARY.search(text)
    if m:
        text = text[:m.start()]

    # 5. Truncate at cross-references: "s. word"
    m = _XREF.search(text)
    if m and m.start() > 10:
        text = text[:m.start()]

    # 6. Take only the core meaning (before first — which introduces variant forms)
    # But keep the first part which is the actual definition
    if '—' in text:
        parts = text.split('—')
        # First part before — is the definition. Rest are compounds/variants.
        text = parts[0]

    # 7. Clean up
    text = re.sub(r'\s+', ' ', text).strip()
    text = text.strip('.,;:— ')

    # 8. Remove leading Nubian word forms that leaked (non-English words)
    # Only strip words containing diacritics (āēīōūáéíóú) — English words don't have those
    _HAS_DIACRITIC = re.compile(r'[āēīōūáéíóúǎǐǒǔñšžčḍṭṣḥ]')
    for _ in range(5):
        # "or hagō stalk" → "stalk" (strip "or + diacritic-word")
        m = re.match(r'^or\s+(\S+)\s+', text)
        if m and _HAS_DIACRITIC.search(m.group(1)):
            text = text[m.end():]
            continue
        # "ágara-kir put" → "put" (strip leading word WITH diacritics)
        m = re.match(r'^(\S+)\s*[,;]\s*', text)
        if m and _HAS_DIACRITIC.search(m.group(1)):
            text = text[m.end():]
            continue
        # Compound with hyphen containing diacritics: "ágara-kir put"
        m = re.match(r'^(\S*-\S+)\s+(?=[a-z])', text)
        if m and _HAS_DIACRITIC.search(m.group(1)):
            text = text[m.end():]
            continue
        # "(Munz.)" "(Almkv.)" "(Rein.)" source references
        text = re.sub(r'^\([A-Z][a-z]+\.?\)\s*', '', text)
        break

    # 9. Remove trailing truncated cognate fragments
    text = re.sub(r'\s+HA\s*$', '', text)
    text = re.sub(r'\s+SE\s*$', '', text)
    text = re.sub(r'\s+and\s*$', '', text)

    # 10. Remove Arabic/Coptic script characters (Old Nubian forms, not English)
    text = re.sub(r'[\u0600-\u06FF\u2C80-\u2CFF\u0370-\u03FF]+\s*', '', text)

    # 11. Clean up again after all stripping
    text = re.sub(r'\s+', ' ', text).strip().strip('.,;:— ')

    return text.strip()


_HAS_DIACRITIC = re.compile(r'[āēīōūáéíóúǎǐǒǔñšžčḍṭṣḥ]')


def split_meanings(definition: str) -> list[str]:
    """Split a cleaned definition into multiple English meanings.

    'penis, nail' → ['penis', 'nail']
    'heart, soul, mind, self' → ['heart', 'soul', 'mind', 'self']
    """
    if not definition:
        return []

    # Split on '; ' first (stronger separator), then ', '
    parts = re.split(r'\s*;\s*', definition)
    meanings = []
    for part in parts:
        sub_parts = re.split(r',\s+(?![^(]*\))', part)
        for sp in sub_parts:
            sp = sp.strip().strip('.,;: ')
            if not sp or len(sp) < 2:
                continue

            # Clean each meaning: strip leading Nubian forms (words with diacritics)
            words = sp.split()
            while words and _HAS_DIACRITIC.search(words[0]):
                words.pop(0)
            sp = " ".join(words).strip()

            # Strip source refs like "(Munz.)" "(Almkv.)" "(Rein.)"
            sp = re.sub(r'\([A-Z][a-z]+\.?\)', '', sp).strip()

            # Strip O.N. references and any remaining cognate fragments
            sp = re.sub(r'^O\.N\.\s*', '', sp)
            sp = re.sub(r'\bO\.N\.\s*', '', sp)

            # Skip meanings that are just cognate references or cross-refs
            if re.match(r'^(s\.\s|cf\.\s|Cf\.\s|in\s+\S+$|only in|or\s+$|\?\s*$)', sp):
                continue

            if sp and len(sp) > 1 and not sp.startswith('HA ') and not sp.startswith('SE '):
                meanings.append(sp)

    return meanings if meanings else [definition]


def parse_entry(li_html: str, page_number: int, entry_idx: int) -> Optional[NubianEntry]:
    """Parse a single <li> or <p> entry into a NubianEntry."""
    headword, rest = extract_headword(li_html)

    if not headword or len(headword) > 40:
        return None

    # Skip page headers like "abā—abi-n"
    if re.match(r'^[a-zā-ž].*—[a-zā-ž]', headword):
        return None

    full_text = strip_tags(li_html)

    dialects = extract_dialects(full_text)
    pos = extract_pos(full_text)
    cognates = extract_cognates(full_text)
    definition = clean_definition(full_text, headword)

    entry = NubianEntry(
        id=f"murray_{page_number}_{entry_idx}",
        entry_type="word",
        headword=headword,
        part_of_speech=pos,
        english=split_meanings(definition),
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


def _is_cognate_only(p_html: str) -> bool:
    """Check if a <p> block is just a cognate/comparative line (right column)."""
    text = strip_tags(p_html).strip()
    # Starts with a language marker
    if re.match(r'^(HAM\.|AR\.|ar\.|SEM\.|NIL\.|CENT\.|EG\.|SUD\.|Cf\.|cf\.)', text):
        return True
    # Starts with a sub-language (Bed., Som., Kham., Copt., etc.)
    if re.match(r'^(Bed\.|Som\.|Kham\.|Copt\.|Bil\.|Qu\.|Sa\.|Ba\.|Haus\.|Mas\.|Nan\.)', text):
        return True
    # Just a cross-reference: "s. word."
    if re.match(r'^s\.\s+[a-záéíóúāēīōūǎǐǒǔ]', text):
        return True
    return False


def extract_p_blocks(html: str) -> list[str]:
    """Extract <p> tag contents as raw HTML strings."""
    return re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)


def parse_page(html: str, page_number: int) -> list[NubianEntry]:
    """Parse all entries from a page's HTML.

    Tries <li> items first. Falls back to <p> blocks for pages where
    the OCR didn't produce list items.
    """
    parser = LiExtractor()
    parser.feed(html)

    items = parser.items

    # Fallback: if no <li> items, use <p> blocks
    if not items:
        p_blocks = extract_p_blocks(html)
        # Filter out cognate-only <p> blocks and merge them into previous entry
        merged = []
        for p_html in p_blocks:
            p_html = p_html.strip()
            if not p_html:
                continue
            if _is_cognate_only(p_html):
                # Attach cognate text to the previous entry
                if merged:
                    merged[-1] += " " + p_html
            else:
                merged.append(p_html)
        items = merged

    entries = []
    for idx, item_html in enumerate(items):
        entry = parse_entry(item_html, page_number, idx)
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

    # Save — versioned output so we can compare iterations
    version = "v7"
    print(f"\nStage 4: Saving {len(all_entries)} entries ({version})...")
    output_path = str(output_dir / f"murray_parsed_{version}.json")
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
