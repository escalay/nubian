#!/usr/bin/env python3
"""
Enrich and normalize the Sambaj dictionary JSON.

Produces sambaj_dictionary_v2.json with:
  1. Hardcoded section boundary map (fixes all section labels)
  2. Verb conjugation grouping (nests inflections under root entries)
  3. Normalized English (corrects book typos, preserves originals)
  4. Normalized romanization (consistent apostrophe style)

Usage:
    python sambaj_enrich.py ../output/sambaj/sambaj_dictionary_clean.json
"""

import json
import re
import sys
from pathlib import Path
from collections import OrderedDict


# ─────────────────────────────────────────────────────────────
# 1. Section Boundary Map (from visual page inspection)
# ─────────────────────────────────────────────────────────────

# page_number → section letter. Derived from checking every page screenshot.
SECTION_MAP = {}

def _fill(letter, start, end):
    for p in range(start, end + 1):
        SECTION_MAP[p] = letter

_fill("A", 15, 21)
_fill("B", 22, 28)
_fill("C", 29, 36)
_fill("D", 37, 42)
_fill("E", 42, 46)   # E starts mid-page 42
_fill("F", 46, 52)   # F starts mid-page 46
_fill("G", 52, 56)   # G starts mid-page 52
_fill("H", 56, 60)   # H starts mid-page 56
_fill("I", 60, 62)   # I starts mid-page 60
_fill("J", 62, 63)   # J starts on 62
_fill("K", 63, 64)   # K starts mid-page 63
_fill("L", 64, 68)   # L starts mid-page 64
_fill("M", 68, 73)   # M starts mid-page 68
_fill("N", 73, 76)   # N starts mid-page 73
_fill("O", 77, 78)
_fill("P", 79, 85)   # P ends mid-page 85
_fill("Q", 84, 85)   # Q starts mid-page 84
_fill("R", 85, 89)   # R starts mid-page 85
_fill("S", 89, 101)  # S starts mid-page 89
_fill("T", 101, 108) # T starts mid-page 101
_fill("U", 107, 108) # U starts mid-page 107
_fill("V", 108, 110) # V starts mid-page 108
_fill("W", 110, 115) # W starts mid-page 110
_fill("Y", 115, 115)


def fix_sections(entries: list[dict]) -> int:
    """Assign sections using the page map + first-letter inference for shared pages."""
    fixed = 0
    for e in entries:
        page = e.get("page_number", 0)
        old = e.get("letter_section", "")

        # For pages that straddle two sections, use the English word's first letter
        eng = e.get("english", [""])[0] if e.get("english") else ""
        clean = re.sub(r'^[^a-zA-Z]*', '', eng)
        word_letter = clean[0].upper() if clean else ""

        # Get the section(s) this page could belong to
        page_section = SECTION_MAP.get(page, old)

        # If word starts with a letter that's valid for this page range, use it
        if word_letter and word_letter == page_section:
            new = word_letter
        elif word_letter and page > 0:
            # Check if adjacent pages have different sections (boundary page)
            prev_sec = SECTION_MAP.get(page - 1, "")
            next_sec = SECTION_MAP.get(page + 1, "")
            if word_letter == prev_sec:
                new = prev_sec
            elif word_letter == next_sec:
                new = next_sec
            else:
                new = page_section
        else:
            new = page_section

        if new and new != old:
            e["letter_section"] = new
            fixed += 1

    return fixed


# ─────────────────────────────────────────────────────────────
# 2. Verb Conjugation Grouping
# ─────────────────────────────────────────────────────────────

# Patterns that indicate an inflected form of the previous entry
INFLECTION_PATTERNS = [
    (r'^is (\w+ing)$', "present_continuous"),
    (r'^(\w+)s$', "present"),           # burns, aborts, covers
    (r'^(\w+)es$', "present"),          # abashes, abuses
    (r'^(\w+)ed$', "past"),             # burned, abashed, covered
    (r'^was (\w+)$', "past_passive"),   # was absorbed, was able
    (r'^is (\w+)$', "passive"),         # is absorbed, is chosen, is cracked
    (r'^(\w+)ing$', "gerund"),          # coming, boiling, writing
    (r'^has (\w+)$', "perfect"),        # has seduced, has flayed
    (r'^will (\w+)$', "future"),        # will be angry
]


def group_verb_conjugations(entries: list[dict]) -> list[dict]:
    """Group consecutive verb forms into root entries with an inflections field."""
    if not entries:
        return entries

    result = []
    i = 0

    while i < len(entries):
        root = entries[i]
        root_eng = root.get("english", [""])[0].lower() if root.get("english") else ""

        # Skip entries that are already inflections (start with "is ", "was ", etc.)
        if any(root_eng.startswith(p) for p in ("is ", "was ", "has ", "will ")):
            result.append(root)
            i += 1
            continue

        # Look ahead for inflections of this root
        inflections = {}
        j = i + 1

        while j < len(entries) and j <= i + 6:  # max 6 inflections ahead
            candidate = entries[j]
            cand_eng = candidate.get("english", [""])[0].lower() if candidate.get("english") else ""

            # Must be on same or adjacent page
            if abs(candidate.get("page_number", 0) - root.get("page_number", 0)) > 1:
                break

            matched = False
            for pattern, label in INFLECTION_PATTERNS:
                if re.match(pattern, cand_eng):
                    # Verify it's derived from the root (shares stem)
                    root_stem = root_eng.rstrip("e")
                    if root_stem in cand_eng or cand_eng.startswith("is " + root_eng[:3]) or cand_eng.startswith("was " + root_eng[:3]):
                        inflections[label] = {
                            k: v for k, v in candidate.items()
                            if k not in ("letter_section", "page_number", "source_page_image")
                            and v
                        }
                        matched = True
                        break

            if not matched:
                break
            j += 1

        if inflections:
            root["inflections"] = inflections
            result.append(root)
            i = j  # skip past all consumed inflections
        else:
            result.append(root)
            i += 1

    return result


# ─────────────────────────────────────────────────────────────
# 3. English Typo Corrections
# ─────────────────────────────────────────────────────────────

ENGLISH_TYPOS = {
    "buteher": "butcher",
    "cherp": "chirp",
    "cressent": "crescent",
    "beanty": "beauty",
    "bunsh": "bunch",
    "chamelion": "chameleon",
    "collapsa": "collapse",
    "cisor": "scissor",
    "creap": "creep",
    "boiledcereal": "boiled cereal",
    "farfarewell": "farewell",
    "al opecia": "alopecia",
    "rivershore": "rivershore",
    "yester day": "yesterday",
    "whit low": "whitlow",
    "neighbour hood": "neighbourhood",
    "quarrel some": "quarrelsome",
    "grass hopper": "grasshopper",
    "sheep fold": "sheepfold",
    "chooped straw": "chopped straw",
    "unskil- ful": "unskillful",
    "tail- less": "tailless",
    "wall- shade": "wall-shade",
    "abat": "abate",
    "remorie": "remorse",
    "barly": "barley",
    "butt": "butt",  # correct as-is
    "cassieauetif oli": "cassia nettle oil",  # already fixed but just in case
}


def add_normalized_english(entries: list[dict]) -> int:
    """Add normalized_english field with corrected spellings."""
    fixed = 0
    for e in entries:
        original = e.get("english", [])
        normalized = []
        changed = False

        for word in original:
            lower = word.lower().strip()
            if lower in ENGLISH_TYPOS:
                normalized.append(ENGLISH_TYPOS[lower])
                changed = True
            else:
                # Check partial matches (for compound words)
                norm = word
                for typo, correction in ENGLISH_TYPOS.items():
                    if typo in word.lower():
                        norm = re.sub(re.escape(typo), correction, word, flags=re.IGNORECASE)
                        changed = True
                        break
                normalized.append(norm)

        if changed:
            e["normalized_english"] = normalized
            fixed += 1

    return fixed


# ─────────────────────────────────────────────────────────────
# 4. Romanization Normalization
# ─────────────────────────────────────────────────────────────

def normalize_romanization(entries: list[dict]) -> int:
    """Normalize apostrophe variants in K-D and F-M romanization columns.

    Unifies: ` (backtick), ' (curly), ' (straight) → ' (straight apostrophe)
    """
    fixed = 0
    for e in entries:
        for field in ["kenzi_dongolawi_roman", "fadija_mahas_roman"]:
            vals = e.get(field, [])
            normalized = []
            for v in vals:
                n = v.replace("\u2018", "'").replace("\u2019", "'").replace("`", "'")
                normalized.append(n)
            if normalized != vals:
                e[field] = normalized
                fixed += 1
    return fixed


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python sambaj_enrich.py <dictionary_clean.json>")
        sys.exit(1)

    path = Path(sys.argv[1])
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    entries = data["entries"]
    print(f"Loaded {len(entries)} entries")

    # 1. Fix sections
    n = fix_sections(entries)
    print(f"  1. Section fixes: {n}")

    # 2. Normalize romanization (before grouping)
    n = normalize_romanization(entries)
    print(f"  4. Romanization normalized: {n} fields")

    # 3. Add normalized English
    n = add_normalized_english(entries)
    print(f"  3. English typos corrected: {n} entries")

    # 4. Group verb conjugations
    before = len(entries)
    entries = group_verb_conjugations(entries)
    grouped = before - len(entries)
    print(f"  2. Verb grouping: {before} → {len(entries)} entries ({grouped} inflections nested)")

    # Update metadata
    data["entries"] = entries
    data["metadata"]["total_entries"] = len(entries)
    data["metadata"]["total_inflections"] = grouped
    data["metadata"]["pipeline_version"] = "0.3.0"

    # Section stats
    from collections import Counter
    sections = Counter(e.get("letter_section", "?") for e in entries)
    print(f"\n  Sections: {dict(sorted(sections.items()))}")

    # Write output
    output_path = path.parent / "sambaj_dictionary_v2.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n  Output: {output_path}")
    print(f"  Root entries: {len(entries)}")
    has_inflections = sum(1 for e in entries if e.get("inflections"))
    print(f"  Entries with inflections: {has_inflections}")


if __name__ == "__main__":
    main()
