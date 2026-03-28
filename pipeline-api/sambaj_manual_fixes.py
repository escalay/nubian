#!/usr/bin/env python3
"""
Manual corrections for the Sambaj dictionary JSON.

Each fix is derived from visually comparing the screenshot against the parsed output.
Run after the pipeline to produce a cleaned JSON.

Usage:
    python sambaj_manual_fixes.py ../output/sambaj/sambaj_dictionary.json
"""

import json
import re
import sys
from pathlib import Path


def clean_newlines(entries: list[dict]) -> int:
    """Strip newlines and collapse whitespace in all text fields."""
    fixed = 0
    for e in entries:
        for field in ["english", "kenzi_dongolawi_roman", "fadija_mahas_roman",
                       "arabic", "kenzi_dongolawi_script", "fadija_script"]:
            vals = e.get(field, [])
            cleaned = [re.sub(r'\s+', ' ', v).strip() for v in vals]
            if cleaned != vals:
                e[field] = cleaned
                fixed += 1
    return fixed


# Manual corrections keyed by (page_number, english[0] substring)
# Each value is a dict of fields to overwrite
MANUAL_FIXES = [
    # ── Newline-wrapped entries (p107-108) ──
    {
        "match": {"page": 107, "english_contains": "uncle"},
        "fixes": [
            {"english_contains": "father", "set": {"english": ["uncle (father's brother)"]}},
            {"english_contains": "mother", "set": {"english": ["uncle (mother's brother)"]}},
        ]
    },
    {
        "match": {"page": 108, "english_contains": "vessel made"},
        "set": {
            "english": ["vessel made of palm leaf"],
            "arabic": ["محفة من الخوص لنقل الاشياء"],
        }
    },

    # ── Column bleed: castor oil (p29) ──
    # Arabic absorbed Kenzi+Fadija text. From screenshot:
    # K-D: ubkogne-inn, F-M: oy ukkognein noy, Arabic: زيت خروع
    # Kenzi: أب كونيح إن ثوى, Fadija: أى أكُوقنِين نوى
    {
        "match": {"page": 29, "english_contains": "castor oil"},
        "set": {
            "kenzi_dongolawi_roman": ["ubkogne - inn"],
            "fadija_mahas_roman": ["oy ukkognein noy"],
            "arabic": ["زيت خروع"],
            "kenzi_dongolawi_script": ["أب كونيح إن ثوى"],
            "fadija_script": ["أى أكُوقنِين نوى"],
        }
    },

    # ── Column bleed: costs (p32) ──
    # From screenshot: K-D: kotti,kobi  F-M: koffin, kella  Arabic: يساوى
    # Kenzi: كُوتِي - كُومِي  Fadija: كُوفِن - كَبَلَا
    {
        "match": {"page": 32, "english_contains": "costs"},
        "set": {
            "arabic": ["يساوى"],
            "kenzi_dongolawi_script": ["كُوتِي", "كُومِي"],
            "fadija_script": ["كُوفِن", "كَبَلَا"],
        }
    },

    # ── Missing scripts: dried deaven bread (p26) ──
    # Continuation row — all dashes in original. Keep as-is but it's a sub-entry.
    # No script data to add.

    # ── Missing scripts: food fisstly (p44) ──
    # From screenshot: this is a continuation of "entertainment" above
    # English: "food firstly", no K-D/F-M, Arabic: فور حضوره, no scripts
    # This is a sub-phrase, not a standalone entry. Mark for context.
    {
        "match": {"page": 44, "english_contains": "food fisstly"},
        "set": {"english": ["food firstly"]},  # fix OCR typo
    },

    # ── Missing scripts: run with current (p50) ──
    # Continuation of "flow" above. Arabic: مع التيار. No standalone scripts.

    # ── Missing scripts: p71 empty english ──
    # From screenshot: continuation of "miser" — sub-line with F-M "wige'i", Arabic "على الطعام"

    # ── Missing scripts: paliva (p74) → should be "nigella" ──
    # From screenshot: English "nigella", K-D: ougoundi, F-M: ouroum, Arabic: حب البركة
    # Kenzi: أقوندي, Fadija: أورُوم
    {
        "match": {"page": 74, "english_contains": "paliva"},
        "set": {
            "english": ["nigella"],
            "kenzi_dongolawi_script": ["أقوندي"],
            "fadija_script": ["أورُوم"],
        }
    },

    # ── Missing scripts: rivershore (p80) ──
    # Continuation of "place" above. Arabic: النهر. Sub-entry.

    # ── Missing scripts: wall with - / mud or plaster (p81) ──
    # These are TWO continuation rows of "plaster" above.
    # "wall with mud or plaster" = one phrase split across rows

    # ── Missing scripts: water (p99) ──
    # Continuation of "sweep". Arabic: الأرض بالماء = "sweep the ground with water"

    # ── Missing scripts: storing food (p108) ──
    # Continuation of "vessel for". Arabic: الطعام

    # ── Missing scripts: plates (p111) ──
    # Continuation of "wash face". Arabic: الأواني الخ = "dishes etc."

    # ── OCR typos in English ──
    {
        "match": {"page": 29, "english_contains": "cassieauetif"},
        "set": {"english": ["cassia nettle oil"]},
    },
    {
        "match": {"page": 59, "english_contains": "how are you"},
        "set": {"english": ["how are you?"]},
    },

    # ── Section boundary fixes ──
    # Pages where the last entries of section X got labeled as section X
    # but should be section X+1 (or vice versa).
    # These are inferred from the first-word heuristic being wrong at boundaries.
    # Fix: entries on boundary pages where English starts with the next letter.
]


def apply_match_fix(entry: dict, fix: dict) -> bool:
    """Apply a single fix if it matches."""
    match = fix.get("match", {})
    page = match.get("page")
    eng_contains = match.get("english_contains", "")

    if page and entry.get("page_number") != page:
        return False
    if eng_contains:
        eng_text = " ".join(entry.get("english", []))
        if eng_contains.lower() not in eng_text.lower():
            return False

    # Handle nested fixes (multiple entries on same page)
    sub_fixes = fix.get("fixes")
    if sub_fixes:
        for sf in sub_fixes:
            eng_text = " ".join(entry.get("english", []))
            if sf.get("english_contains", "").lower() in eng_text.lower():
                for k, v in sf["set"].items():
                    entry[k] = v
                return True
        return False

    # Direct fix
    updates = fix.get("set", {})
    if updates:
        for k, v in updates.items():
            entry[k] = v
        return True

    return False


def fix_section_boundaries(entries: list[dict]) -> int:
    """Fix section labels at page boundaries using alphabetical ordering of English words."""
    fixed = 0
    current_section = ""

    for e in entries:
        section = e.get("letter_section", "")
        eng = e.get("english", [""])[0] if e.get("english") else ""

        # Determine what section this entry should be in based on its English word
        clean_eng = re.sub(r'^[^a-zA-Z]*', '', eng)
        if not clean_eng:
            continue

        expected_section = clean_eng[0].upper()

        # Only fix if the entry's word clearly belongs to a different section
        # AND the word is a simple single word (not a phrase that starts with a common word)
        skip_prefixes = {"is", "was", "will", "has", "the", "to", "a", "an", "be",
                         "how", "one", "last", "next", "very", "dried", "raw", "fresh",
                         "take", "come", "go", "give", "make", "run", "pick", "put",
                         "cut", "blow", "open", "play", "shut", "swap", "self", "send"}
        first_word = clean_eng.split()[0].lower() if clean_eng else ""

        if first_word in skip_prefixes:
            continue

        # Only fix if it's a single word AND the section is wrong
        if " " not in eng and expected_section != section and expected_section >= current_section:
            e["letter_section"] = expected_section
            fixed += 1

        if e.get("letter_section"):
            current_section = e["letter_section"]

    return fixed


def main():
    if len(sys.argv) < 2:
        print("Usage: python sambaj_manual_fixes.py <dictionary.json>")
        sys.exit(1)

    path = Path(sys.argv[1])
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    entries = data["entries"]
    print(f"Loaded {len(entries)} entries")

    # 1. Clean newlines
    n = clean_newlines(entries)
    print(f"  Cleaned newlines: {n} fields")

    # 2. Apply manual fixes
    manual_count = 0
    for entry in entries:
        for fix in MANUAL_FIXES:
            if apply_match_fix(entry, fix):
                manual_count += 1
    print(f"  Manual fixes applied: {manual_count}")

    # 3. Fix section boundaries
    n = fix_section_boundaries(entries)
    print(f"  Section boundary fixes: {n}")

    # 4. Remove quality_warnings for fixed entries
    warnings = data.get("quality_warnings", [])
    fixed_keys = {("castor oil", 29), ("costs", 32)}
    warnings = [w for w in warnings if (w["english"][0], w["page"]) not in fixed_keys]
    data["quality_warnings"] = warnings
    print(f"  Remaining warnings: {len(warnings)}")

    # 5. Update metadata
    data["metadata"]["pipeline_version"] = "0.2.0"
    data["metadata"]["total_entries"] = len(entries)

    # Write output
    output_path = path.parent / "sambaj_dictionary_clean.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n  Output: {output_path}")

    # Verify
    from collections import Counter
    sections = Counter(e.get("letter_section", "?") for e in entries)
    print(f"  Sections: {dict(sorted(sections.items()))}")


if __name__ == "__main__":
    main()
