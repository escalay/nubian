#!/usr/bin/env python3
"""
Stage 2: Parse Chandra OCR HTML output into structured dictionary entries.

This script takes the HTML output from Stage 1 (Chandra OCR) and extracts
structured dictionary entries from Armbruster's Dongolese Nubian Lexicon.

Key parsing rules based on Armbruster's typographic conventions:
  - <b> / <strong> = headword or sub-entry
  - <i> / <em> = English translation/gloss
  - [] = etymology (may contain Arabic script)
  - () = variant forms, phonological alternations
  - § = cross-references to companion grammar
  - ˘ = morpheme juncture (MUST be preserved)
  - Indentation = hierarchical nesting of sub-entries

Usage:
    python stage2_parse.py --input ./ocr_output --output ./parsed_entries.json

    # With validation report
    python stage2_parse.py --input ./ocr_output --output ./parsed_entries.json --validate
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from html.parser import HTMLParser


# ─────────────────────────────────────────────────────────────
# Data Model
# ─────────────────────────────────────────────────────────────

@dataclass
class Definition:
    sense_label: Optional[str] = None  # e.g., "(a)", "(b)"
    gloss: str = ""

@dataclass
class UsageExample:
    nubian: str = ""
    english: str = ""
    morphophonological_notes: Optional[str] = None

@dataclass
class Compound:
    form: str = ""
    gloss: str = ""

@dataclass
class DictionaryEntry:
    headword: str = ""
    parent_headword: Optional[str] = None
    variant_forms: list = field(default_factory=list)
    part_of_speech: Optional[str] = None
    grammar_references: list = field(default_factory=list)
    definitions: list = field(default_factory=list)
    etymology: Optional[str] = None
    possessive_paradigm: Optional[dict] = None
    inflections: dict = field(default_factory=dict)
    compounds: list = field(default_factory=list)
    usage_examples: list = field(default_factory=list)
    cultural_notes: Optional[str] = None
    cross_references: list = field(default_factory=list)
    page_number: Optional[int] = None
    raw_text: str = ""  # Preserve the raw text for validation

    def to_dict(self):
        d = asdict(self)
        # Clean up None values and empty collections
        d["definitions"] = [asdict(Definition(**dd)) if isinstance(dd, dict) else asdict(dd)
                           for dd in self.definitions]
        d["usage_examples"] = [asdict(UsageExample(**ue)) if isinstance(ue, dict) else asdict(ue)
                               for ue in self.usage_examples]
        d["compounds"] = [asdict(Compound(**c)) if isinstance(c, dict) else asdict(c)
                         for c in self.compounds]
        return d


# ─────────────────────────────────────────────────────────────
# HTML → Annotated Text Converter
# ─────────────────────────────────────────────────────────────

class AnnotatedSpan:
    """A span of text with formatting annotations."""
    def __init__(self, text: str, bold: bool = False, italic: bool = False):
        self.text = text
        self.bold = bold
        self.italic = italic

    def __repr__(self):
        flags = []
        if self.bold: flags.append("B")
        if self.italic: flags.append("I")
        return f"[{''.join(flags)}]{self.text}"


class HTMLToAnnotatedText(HTMLParser):
    """Convert HTML to a list of AnnotatedSpans preserving bold/italic."""

    def __init__(self):
        super().__init__()
        self.spans: list[AnnotatedSpan] = []
        self.bold_depth = 0
        self.italic_depth = 0
        self._current_text = ""

    def _flush(self):
        if self._current_text:
            self.spans.append(AnnotatedSpan(
                text=self._current_text,
                bold=self.bold_depth > 0,
                italic=self.italic_depth > 0,
            ))
            self._current_text = ""

    def handle_starttag(self, tag, attrs):
        if tag in ("b", "strong"):
            self._flush()
            self.bold_depth += 1
        elif tag in ("i", "em"):
            self._flush()
            self.italic_depth += 1
        elif tag == "br":
            self._current_text += "\n"
        elif tag in ("p", "div"):
            self._flush()
            self._current_text += "\n"

    def handle_endtag(self, tag):
        if tag in ("b", "strong"):
            self._flush()
            self.bold_depth = max(0, self.bold_depth - 1)
        elif tag in ("i", "em"):
            self._flush()
            self.italic_depth = max(0, self.italic_depth - 1)
        elif tag in ("p", "div"):
            self._flush()
            self._current_text += "\n"

    def handle_data(self, data):
        self._current_text += data

    def get_spans(self) -> list[AnnotatedSpan]:
        self._flush()
        return self.spans


def html_to_annotated(html: str) -> list[AnnotatedSpan]:
    """Convert HTML string to list of annotated spans."""
    parser = HTMLToAnnotatedText()
    parser.feed(html)
    return parser.get_spans()


# ─────────────────────────────────────────────────────────────
# Regex Patterns for Armbruster's Conventions
# ─────────────────────────────────────────────────────────────

# Section references: §123, §§123-456, §1234ff., §1234a, §1234d
RE_GRAMMAR_REF = re.compile(r'§§?\d+[a-z]?(?:[-–]\d+[a-z]?)?(?:ff\.?)?')

# Part of speech abbreviations
POS_ABBREVS = {
    "n.", "v.t.", "v.i.", "adj.", "adv.", "stat.", "caus.", "prep.",
    "conj.", "interj.", "pron.", "num.", "v.p.", "n. act.", "n. dic.",
    "dem.", "postp.", "app.", "dim.", "coll.", "freq.",
}

# Possessive paradigm prefixes
POSS_PREFIXES = {
    "1sg": r"(?:ám|am|1sg)",
    "2sg": r"(?:ím|im|2sg)",
    "3sg": r"(?:tím|tim|3sg)",
    "1pl": r"(?:antím|antim|1pl)",
    "2pl": r"(?:intím|intim|2pl)",
    "3pl": r"(?:tintím|tintim|3pl)",
}

# Inflection labels
INFLECTION_LABELS = [
    "obj", "pl", "gen", "voc",
    "ind. pres", "ind.pres", "perf", "imperat",
    "part. pres", "part.pres", "part. past", "part.past",
]

# Etymology: content inside square brackets
RE_ETYMOLOGY = re.compile(r'\[([^\]]*(?:\[[^\]]*\])*[^\]]*)\]')

# Variant forms in parentheses at start of entry
RE_VARIANT = re.compile(r'\(([^)]+)\)')

# Sense labels
RE_SENSE_LABEL = re.compile(r'^\s*\(([a-z])\)\s*')

# The breve/connector symbol (multiple representations)
CONNECTOR_CHARS = "˘\u0306\u0361"  # breve, combining breve, combining double inverted breve

# Derived verb form suffixes (for identifying sub-entries)
DERIVED_SUFFIXES = [
    "-an", "-bü-", "-e-dól-", "-e-má-", "-katti-",
    "-e-nóg-", "-e-tá-", "-ed-ág-", "-ing-ir", "-rk-ir",
    "-ir", "-ág-", "-nóg-", "-eg-ág-",
]


# ─────────────────────────────────────────────────────────────
# Entry Boundary Detection
# ─────────────────────────────────────────────────────────────

def find_entry_boundaries(spans: list[AnnotatedSpan]) -> list[int]:
    """
    Find indices in the span list where new dictionary entries begin.

    A new entry starts when we see bold text that looks like a headword:
    - At the start of a line (after newline)
    - Bold text that is NOT just a grammar label or number
    """
    boundaries = []
    full_text = "".join(s.text for s in spans)

    # Build a position map: for each character position, which span is it in?
    char_to_span = []
    for i, span in enumerate(spans):
        for _ in span.text:
            char_to_span.append(i)

    # Walk through spans looking for bold text at line beginnings
    pos = 0
    for i, span in enumerate(spans):
        if span.bold and span.text.strip():
            text = span.text.strip()

            # Skip if it's just a number or grammar reference
            if re.match(r'^[\d§()]+$', text):
                pos += len(span.text)
                continue

            # Skip if it looks like a sense label
            if re.match(r'^\([a-z]\)$', text):
                pos += len(span.text)
                continue

            # Check if this is at the start of content or after a newline
            preceding_text = full_text[:pos].rstrip()
            if pos == 0 or preceding_text.endswith("\n") or preceding_text == "":
                boundaries.append(i)
            elif i > 0 and not spans[i-1].bold:
                # Bold text appearing after non-bold text on a new logical line
                prev_text = spans[i-1].text
                if "\n" in prev_text or prev_text.strip() == "":
                    boundaries.append(i)

        pos += len(span.text)

    return boundaries


# ─────────────────────────────────────────────────────────────
# Entry Parser
# ─────────────────────────────────────────────────────────────

def extract_grammar_refs(text: str) -> list[str]:
    """Extract all §-references from text."""
    return RE_GRAMMAR_REF.findall(text)


def extract_etymology(text: str) -> Optional[str]:
    """Extract etymology from square brackets."""
    match = RE_ETYMOLOGY.search(text)
    if match:
        return match.group(0)  # Include brackets
    return None


def extract_part_of_speech(text: str) -> Optional[str]:
    """Extract part of speech abbreviation."""
    for pos in sorted(POS_ABBREVS, key=len, reverse=True):
        if pos in text:
            return pos
    return None


def extract_definitions(italic_spans: list[str], full_text: str) -> list[Definition]:
    """Extract English definitions from italic text spans."""
    definitions = []

    for gloss_text in italic_spans:
        gloss_text = gloss_text.strip()
        if not gloss_text or len(gloss_text) < 2:
            continue

        # Skip if it looks like a usage example (contains Nubian text nearby)
        # Simple heuristic: standalone glosses tend to be shorter
        sense_match = RE_SENSE_LABEL.match(gloss_text)
        if sense_match:
            definitions.append(Definition(
                sense_label=sense_match.group(1),
                gloss=gloss_text[sense_match.end():].strip()
            ))
        else:
            definitions.append(Definition(gloss=gloss_text))

    return definitions


def parse_entry_spans(spans: list[AnnotatedSpan], page_number: int = None,
                      parent_hw: str = None) -> DictionaryEntry:
    """
    Parse a sequence of annotated spans into a DictionaryEntry.
    """
    entry = DictionaryEntry(page_number=page_number, parent_headword=parent_hw)

    if not spans:
        return entry

    # Extract headword from first bold span
    for span in spans:
        if span.bold and span.text.strip():
            entry.headword = span.text.strip()
            break

    # Build full text and collect italic spans
    full_text = ""
    italic_texts = []
    bold_texts = []

    for span in spans:
        full_text += span.text
        if span.italic and not span.bold:
            italic_texts.append(span.text)
        if span.bold:
            bold_texts.append(span.text)

    entry.raw_text = full_text.strip()

    # Extract structured fields
    entry.grammar_references = extract_grammar_refs(full_text)
    entry.etymology = extract_etymology(full_text)
    entry.part_of_speech = extract_part_of_speech(full_text)
    entry.cross_references = [ref for ref in extract_grammar_refs(full_text)]

    # Extract variant forms from parentheses near the headword
    # Look in the first ~100 chars after the headword
    hw_end = full_text.find(entry.headword) + len(entry.headword) if entry.headword else 0
    early_text = full_text[hw_end:hw_end + 150]
    variants = RE_VARIANT.findall(early_text)
    entry.variant_forms = [v for v in variants if not re.match(r'^[a-z]$', v) and "§" not in v]

    # Extract definitions from italic text
    if italic_texts:
        entry.definitions = extract_definitions(italic_texts, full_text)

    # Extract usage examples (Nubian in roman, English in italic)
    # Pattern: non-italic text followed by italic text
    examples = []
    i = 0
    while i < len(spans) - 1:
        if (not spans[i].italic and not spans[i].bold and
            spans[i].text.strip() and
            i + 1 < len(spans) and spans[i+1].italic):

            nubian = spans[i].text.strip()
            english = spans[i+1].text.strip()

            # Heuristic: usage examples tend to be longer and sentence-like
            if len(nubian) > 10 and len(english) > 5:
                examples.append(UsageExample(nubian=nubian, english=english))
        i += 1
    entry.usage_examples = examples

    # Extract inflections
    inflections = {}
    for label in INFLECTION_LABELS:
        pattern = re.compile(rf'{re.escape(label)}\.?\s+([^\s,;.]+)', re.IGNORECASE)
        match = pattern.search(full_text)
        if match:
            inflections[label.replace(".", "").replace(" ", "_")] = match.group(1)

    if inflections:
        entry.inflections = inflections

    return entry


# ─────────────────────────────────────────────────────────────
# Page Processor
# ─────────────────────────────────────────────────────────────

def process_page_html(html: str, page_number: int) -> list[DictionaryEntry]:
    """Process a single page's HTML into dictionary entries."""
    spans = html_to_annotated(html)

    if not spans:
        return []

    # Find entry boundaries
    boundaries = find_entry_boundaries(spans)

    if not boundaries:
        # Try treating the whole page as one entry
        entry = parse_entry_spans(spans, page_number)
        return [entry] if entry.headword else []

    entries = []
    for idx, start in enumerate(boundaries):
        end = boundaries[idx + 1] if idx + 1 < len(boundaries) else len(spans)
        entry_spans = spans[start:end]
        entry = parse_entry_spans(entry_spans, page_number)

        if entry.headword:
            entries.append(entry)

    return entries


def assign_parent_headwords(entries: list[DictionaryEntry]) -> list[DictionaryEntry]:
    """
    Assign parent_headword relationships based on derivational morphology.

    A derived entry like 'bér-an' should have parent_headword 'bér'.
    """
    root_entries = {}

    for entry in entries:
        hw = entry.headword

        # Check if this looks like a derived form
        for suffix in DERIVED_SUFFIXES:
            if suffix in hw:
                # The root is everything before the suffix
                potential_root = hw[:hw.index(suffix)].rstrip("-")

                # Look for this root in our entries
                if potential_root in root_entries:
                    entry.parent_headword = potential_root
                    break

        # Register this as a potential root
        root_entries[hw] = entry

    return entries


# ─────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────

def validate_entries(entries: list[DictionaryEntry]) -> dict:
    """Run validation checks on extracted entries."""
    report = {
        "total_entries": len(entries),
        "entries_with_headword": sum(1 for e in entries if e.headword),
        "entries_with_definitions": sum(1 for e in entries if e.definitions),
        "entries_with_etymology": sum(1 for e in entries if e.etymology),
        "entries_with_grammar_refs": sum(1 for e in entries if e.grammar_references),
        "entries_with_pos": sum(1 for e in entries if e.part_of_speech),
        "entries_with_inflections": sum(1 for e in entries if e.inflections),
        "entries_with_usage_examples": sum(1 for e in entries if e.usage_examples),
        "entries_with_parent": sum(1 for e in entries if e.parent_headword),
        "connector_symbol_preserved": 0,
        "arabic_script_detected": 0,
        "orphan_parents": [],
    }

    parent_set = {e.headword for e in entries}
    for e in entries:
        # Check ˘ preservation
        if any(c in e.headword for c in CONNECTOR_CHARS):
            report["connector_symbol_preserved"] += 1
        if any(c in e.raw_text for c in CONNECTOR_CHARS):
            report["connector_symbol_preserved"] += 1

        # Check Arabic script
        if any("\u0600" <= c <= "\u06FF" for c in e.raw_text):
            report["arabic_script_detected"] += 1

        # Check orphan parent references
        if e.parent_headword and e.parent_headword not in parent_set:
            report["orphan_parents"].append({
                "entry": e.headword,
                "missing_parent": e.parent_headword
            })

    return report


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Stage 2: Parse OCR output into structured entries")
    parser.add_argument("--input", "-i", required=True,
                        help="Directory containing Stage 1 HTML output")
    parser.add_argument("--output", "-o", required=True,
                        help="Output JSON file path")
    parser.add_argument("--validate", action="store_true",
                        help="Run validation checks and print report")
    parser.add_argument("--pretty", action="store_true",
                        help="Pretty-print JSON output")
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_path = Path(args.output)

    if not input_dir.exists():
        print(f"Error: Input directory not found: {input_dir}")
        sys.exit(1)

    # Find all HTML files
    html_files = sorted(input_dir.glob("page_*.html"))
    if not html_files:
        print(f"Error: No page_*.html files found in {input_dir}")
        sys.exit(1)

    print(f"Found {len(html_files)} HTML page files")

    # Process each page
    all_entries = []
    for html_file in html_files:
        # Extract page number from filename
        page_num_str = html_file.stem.replace("page_", "")
        try:
            page_num = int(page_num_str) + 1  # Convert back to 1-indexed
        except ValueError:
            page_num = None

        html = html_file.read_text(encoding="utf-8")
        entries = process_page_html(html, page_num)
        all_entries.extend(entries)
        print(f"  {html_file.name}: {len(entries)} entries")

    # Assign parent-child relationships
    print(f"\nAssigning parent headword relationships...")
    all_entries = assign_parent_headwords(all_entries)

    # Serialize
    output_data = {
        "metadata": {
            "source": "Armbruster, C.H. (1965). Dongolese Nubian: A Lexicon. Cambridge University Press.",
            "pipeline_version": "0.1.0",
            "total_entries": len(all_entries),
        },
        "entries": [e.to_dict() for e in all_entries],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    indent = 2 if args.pretty else None
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=indent)

    print(f"\n✓ Wrote {len(all_entries)} entries to {output_path}")

    # Validation
    if args.validate:
        print(f"\n{'='*60}")
        print("VALIDATION REPORT")
        print(f"{'='*60}")
        report = validate_entries(all_entries)
        for key, value in report.items():
            if isinstance(value, list):
                print(f"  {key}: {len(value)} issues")
                for item in value[:10]:
                    print(f"    - {item}")
            else:
                print(f"  {key}: {value}")

        # Save validation report
        report_path = output_path.with_suffix(".validation.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n  Validation report saved to: {report_path}")


if __name__ == "__main__":
    main()
