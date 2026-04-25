#!/usr/bin/env python3
"""Build and validate the unified Nubian corpus.

The source pipelines intentionally keep their own raw output shapes. This tool
normalizes those outputs into one stable corpus without discarding source
records, then derives app-facing datasets for search, flashcards, dialect
comparison, and proverb practice.

Usage:
    python pipeline-api/unified_corpus_merger.py all
    python pipeline-api/unified_corpus_merger.py qa
    python pipeline-api/unified_corpus_merger.py merge
    python pipeline-api/unified_corpus_merger.py canonical
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "output"
CORPUS_DIR = OUTPUT_DIR / "corpus"
APP_DIR = OUTPUT_DIR / "app"

MURRAY_CANONICAL_DIR = OUTPUT_DIR / "murray" / "llm-canonical"
MURRAY_COMBINED_PATH = MURRAY_CANONICAL_DIR / "murray_canonical.json"
UNIFIED_CORPUS_PATH = CORPUS_DIR / "unified_corpus.json"
CANONICAL_WORDS_PATH = CORPUS_DIR / "canonical_words.json"


SOURCE_INFO = {
    "sambaj": {
        "short": "Sambaj",
        "title": "The Nubian Dictionary",
        "author": "Youssef Sambaj",
        "dialects": ["KD", "FM"],
    },
    "satzinger": {
        "short": "Satzinger",
        "title": "Short Archaeological Wordlist in English, Sudani Arabic and Nobiin",
        "author": "Helmut Satzinger",
        "year": 2018,
        "dialects": ["FM"],
    },
    "proverbs": {
        "short": "Habbob",
        "title": "Nubian Proverbs (Fadijja/Mahas)",
        "author": "Maher Habbob",
        "year": 2020,
        "dialects": ["FM"],
    },
    "murray": {
        "short": "Murray",
        "title": "An English-Nubian Comparative Dictionary",
        "author": "G.W. Murray",
        "year": 1923,
        "dialects": ["K", "D", "M", "KD", "KDM", "Dai", "Mid"],
    },
    "armbruster": {
        "short": "Armbruster",
        "title": "Dongolese Nubian: A Lexicon",
        "author": "C.H. Armbruster",
        "year": 1965,
        "dialects": ["D"],
    },
}


ARMBRUSTER_LLM_PATH = OUTPUT_DIR / "armbruster" / "llm-columns" / "armbruster_columns.json"
SOURCE_PRIORITY = {
    "armbruster": 0,
    "murray": 1,
    "sambaj": 2,
    "satzinger": 3,
    "proverbs": 4,
}
GLOSS_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "onto",
    "that",
    "this",
    "there",
    "their",
    "they",
    "them",
    "his",
    "her",
    "its",
    "one",
    "some",
    "any",
    "kind",
    "thing",
    "person",
    "etc",
    "make",
    "made",
    "have",
    "has",
    "had",
    "being",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def clean_list(value: Any) -> list[str]:
    seen = set()
    values = []
    for item in as_list(value):
        cleaned = clean_string(item)
        if cleaned and cleaned not in seen:
            values.append(cleaned)
            seen.add(cleaned)
    return values


def normalize_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.lower()
    normalized = normalized.replace("˘", "").replace("ʿ", "").replace("'", "")
    normalized = re.sub(r"[^a-z0-9\u0600-\u06ff]+", "", normalized)
    return normalized


def first_non_empty(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            for item in value:
                cleaned = clean_string(item)
                if cleaned:
                    return cleaned
        else:
            cleaned = clean_string(value)
            if cleaned:
                return cleaned
    return ""


def source_ref(book: str, page: int | None = None, screenshot: str | None = None) -> dict[str, Any]:
    ref: dict[str, Any] = {"book": book}
    if page:
        ref["page"] = page
    if screenshot:
        ref["screenshot"] = screenshot
    return ref


def count_files(path: Path, pattern: str = "*") -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.glob(pattern) if item.is_file())


def page_number_from_path(path: Path) -> int:
    match = re.search(r"page_(\d+)", path.name)
    return int(match.group(1)) if match else 0


def rebuild_murray_canonical() -> dict[str, Any]:
    page_paths = sorted(MURRAY_CANONICAL_DIR.glob("page_*.json"))
    pages = []
    total_entries = 0

    for page_path in page_paths:
        extraction = read_json(page_path)
        entries = extraction.get("entries", [])
        total_entries += len(entries)
        pages.append({
            "pdf_page": page_number_from_path(page_path),
            "extraction": extraction,
        })

    combined = {
        "metadata": {
            "source": "Murray 1923",
            "method": "Canonical: Gemini 3 Flash (thinking=high) + OCR hints + LLM enrichment",
            "model": "google/gemini-3-flash-preview",
            "enrichments": [
                "arabic_translation",
                "ipa",
                "simple_roman",
                "categories",
                "difficulty",
                "root",
                "is_loanword",
                "is_archaic",
                "example_sentence",
            ],
            "pages_processed": len(pages),
            "total_entries": total_entries,
            "rebuilt_at": utc_now(),
        },
        "pages": pages,
    }
    write_json(MURRAY_COMBINED_PATH, combined)
    return combined["metadata"]


def load_murray_page_entries() -> list[tuple[int, dict[str, Any]]]:
    entries = []
    for page_path in sorted(MURRAY_CANONICAL_DIR.glob("page_*.json")):
        page = read_json(page_path)
        page_number = int(page.get("book_page_number") or page_number_from_path(page_path))
        for entry in page.get("entries", []):
            entries.append((page_number, entry))
    return entries


def build_manifest() -> dict[str, Any]:
    source_files = {
        "sambaj": OUTPUT_DIR / "sambaj" / "sambaj_dictionary_v2.json",
        "satzinger": OUTPUT_DIR / "satzinger" / "satzinger_parsed.json",
        "proverbs": OUTPUT_DIR / "proverbs" / "proverbs_parsed.json",
        "armbruster": OUTPUT_DIR / "armbruster" / "parsed" / "armbruster_parsed.json",
    }

    manifest: dict[str, Any] = {
        "generated_at": utc_now(),
        "root": str(ROOT_DIR),
        "sources": {},
        "artifacts": {
            "books_file_count": count_files(ROOT_DIR / "books"),
            "output_file_count": count_files(OUTPUT_DIR, "**/*"),
        },
    }

    for source, path in source_files.items():
        data = read_json(path) if path.exists() else {}
        entries = data.get("entries", [])
        manifest["sources"][source] = {
            **SOURCE_INFO[source],
            "entry_count": len(entries),
            "source_file": str(path.relative_to(ROOT_DIR)),
            "exists": path.exists(),
        }

    murray_entries = load_murray_page_entries()
    manifest["sources"]["murray"] = {
        **SOURCE_INFO["murray"],
        "entry_count": len(murray_entries),
        "page_count": count_files(MURRAY_CANONICAL_DIR, "page_*.json"),
        "source_file": str(MURRAY_COMBINED_PATH.relative_to(ROOT_DIR)),
        "exists": MURRAY_COMBINED_PATH.exists(),
    }

    armbruster_dir = OUTPUT_DIR / "armbruster"
    deterministic_pages = deterministic_armbruster_pages()
    llm_continuation = armbruster_llm_continuation_entries(deterministic_pages)
    manifest["sources"]["armbruster"].update({
        "column_image_count": count_files(armbruster_dir / "columns", "*.png"),
        "ocr_column_count": count_files(armbruster_dir / "ocr-columns", "*.html"),
        "parsed_page_count": count_files(armbruster_dir / "parsed", "page_*.json"),
        "deterministic_page_count": len(deterministic_pages),
        "llm_continuation_entry_count": len(llm_continuation),
        "combined_entry_count": manifest["sources"]["armbruster"]["entry_count"] + len(llm_continuation),
    })

    write_json(OUTPUT_DIR / "manifest.json", manifest)
    return manifest


def qa_issue(severity: str, code: str, message: str, **details: Any) -> dict[str, Any]:
    issue = {"severity": severity, "code": code, "message": message}
    if details:
        issue["details"] = details
    return issue


def run_qa() -> dict[str, Any]:
    issues = []

    murray_page_sum = sum(len(entry.get("entries", [])) for entry in (
        read_json(path) for path in sorted(MURRAY_CANONICAL_DIR.glob("page_*.json"))
    ))
    murray_combined_total = None
    if MURRAY_COMBINED_PATH.exists():
        murray_combined_total = read_json(MURRAY_COMBINED_PATH).get("metadata", {}).get("total_entries")
    if murray_combined_total != murray_page_sum:
        issues.append(qa_issue(
            "error",
            "murray_combined_stale",
            "Murray combined metadata does not match per-page entry sum.",
            combined_total=murray_combined_total,
            page_sum=murray_page_sum,
        ))

    armbruster_dir = OUTPUT_DIR / "armbruster"
    expected_pages = range(18, 223)
    missing_column_images = []
    missing_ocr_columns = []
    missing_parsed_pages = []
    for page in expected_pages:
        for column in (1, 2, 3):
            column_image = armbruster_dir / "columns" / f"page_{page:04d}_col{column}.png"
            column_html = armbruster_dir / "ocr-columns" / f"page_{page:04d}_col{column}.html"
            if not column_image.exists():
                missing_column_images.append(f"page_{page:04d}_col{column}")
            if not column_html.exists():
                missing_ocr_columns.append(f"page_{page:04d}_col{column}")
        page_json = armbruster_dir / "parsed" / f"page_{page:04d}.json"
        if not page_json.exists():
            missing_parsed_pages.append(page)

    if missing_column_images:
        issues.append(qa_issue(
            "error",
            "armbruster_missing_column_images",
            "Armbruster has missing 4x column images.",
            count=len(missing_column_images),
            sample=missing_column_images[:10],
        ))
    if missing_ocr_columns:
        issues.append(qa_issue(
            "warning",
            "armbruster_missing_ocr_columns",
            "Armbruster has columns that have not been OCR processed yet.",
            count=len(missing_ocr_columns),
            first_missing=missing_ocr_columns[:10],
        ))
    if missing_parsed_pages:
        issues.append(qa_issue(
            "warning",
            "armbruster_missing_parsed_pages",
            "Armbruster has pages missing parsed JSON output.",
            count=len(missing_parsed_pages),
            first_missing=missing_parsed_pages[:10],
            last_missing=missing_parsed_pages[-10:],
        ))

    source_entries = {
        "sambaj": read_json(OUTPUT_DIR / "sambaj" / "sambaj_dictionary_v2.json").get("entries", []),
        "satzinger": read_json(OUTPUT_DIR / "satzinger" / "satzinger_parsed.json").get("entries", []),
        "proverbs": read_json(OUTPUT_DIR / "proverbs" / "proverbs_parsed.json").get("entries", []),
        "murray": [entry for _, entry in load_murray_page_entries()],
        "armbruster": read_json(OUTPUT_DIR / "armbruster" / "parsed" / "armbruster_parsed.json").get("entries", []),
    }

    source_stats = {}
    for source, entries in source_entries.items():
        missing_english = sum(1 for entry in entries if not clean_list(entry.get("english")))
        headword_keys = Counter(normalize_key(clean_string(entry.get("headword", ""))) for entry in entries)
        duplicates = {key: count for key, count in headword_keys.items() if key and count > 1}
        source_stats[source] = {
            "entries": len(entries),
            "missing_english": missing_english,
            "duplicate_normalized_headwords": len(duplicates),
            "duplicate_samples": dict(list(duplicates.items())[:20]),
        }
        if missing_english:
            issues.append(qa_issue(
                "warning",
                f"{source}_missing_english",
                f"{source} has entries without English glosses.",
                count=missing_english,
                total=len(entries),
            ))

    armbruster_entries = source_entries["armbruster"]
    with_breve = sum(1 for entry in armbruster_entries if "˘" in clean_string(entry.get("headword")))
    armbruster_breve_rate = round(with_breve / max(len(armbruster_entries), 1), 4)

    report = {
        "generated_at": utc_now(),
        "summary": {
            "issue_count": len(issues),
            "errors": sum(1 for issue in issues if issue["severity"] == "error"),
            "warnings": sum(1 for issue in issues if issue["severity"] == "warning"),
            "murray_page_entry_sum": murray_page_sum,
            "murray_combined_total": murray_combined_total,
            "armbruster_breve_rate": armbruster_breve_rate,
        },
        "sources": source_stats,
        "issues": issues,
    }
    write_json(OUTPUT_DIR / "qa_report.json", report)
    return report


def normalize_sambaj(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for index, entry in enumerate(entries, start=1):
        page = entry.get("page_number")
        forms = []
        for value in clean_list(entry.get("kenzi_dongolawi_roman")):
            forms.append({"dialect": "KD", "romanization": value})
        for value in clean_list(entry.get("fadija_mahas_roman")):
            forms.append({"dialect": "FM", "romanization": value})
        for value in clean_list(entry.get("kenzi_dongolawi_script")):
            forms.append({"dialect": "KD", "script": value})
        for value in clean_list(entry.get("fadija_script")):
            forms.append({"dialect": "FM", "script": value})

        headword = first_non_empty(entry.get("kenzi_dongolawi_roman"), entry.get("fadija_mahas_roman"), entry.get("english"))
        normalized.append(normalize_entry(
            source="sambaj",
            source_index=index,
            page=page,
            headword=headword,
            entry_type="word",
            english=entry.get("english"),
            arabic=entry.get("arabic"),
            forms=forms,
            category=entry.get("category", ""),
            section=entry.get("letter_section", ""),
            screenshot=entry.get("source_page_image"),
            raw_entry=entry,
        ))
    return normalized


def normalize_pass_through(source: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for index, entry in enumerate(entries, start=1):
        page = None
        screenshot = None
        for ref in as_list(entry.get("sources")):
            if isinstance(ref, dict) and ref.get("book") == source:
                page = ref.get("page")
                screenshot = ref.get("screenshot")
                break
        normalized.append(normalize_entry(
            source=source,
            source_index=index,
            page=page,
            headword=entry.get("headword", ""),
            entry_type=entry.get("entry_type", "word"),
            english=entry.get("english"),
            arabic=entry.get("arabic"),
            sudani_arabic=entry.get("sudani_arabic"),
            forms=entry.get("forms", []),
            pos=entry.get("pos", ""),
            category=entry.get("category", ""),
            section=entry.get("section", ""),
            etymology=entry.get("etymology", ""),
            cognates=entry.get("cognates", []),
            usage_examples=entry.get("usage_examples", []),
            screenshot=screenshot,
            proverb={
                "text": entry.get("proverb_text", ""),
                "transliteration": entry.get("proverb_transliteration", ""),
                "literal": entry.get("proverb_literal", ""),
                "meaning": entry.get("proverb_meaning", ""),
                "script": entry.get("proverb_nubian_script", ""),
            },
            raw_entry=entry,
        ))
    return normalized


def normalize_murray(entries: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    normalized = []
    for index, (page, entry) in enumerate(entries, start=1):
        forms = []
        for form in as_list(entry.get("forms")):
            if not isinstance(form, dict):
                continue
            form_record = {
                "dialect": clean_string(form.get("dialect")),
                "romanization": clean_string(form.get("form")),
            }
            if form.get("plural"):
                form_record["plural"] = clean_string(form.get("plural"))
            forms.append({key: value for key, value in form_record.items() if value})

        arabic = clean_list([entry.get("arabic_translation"), entry.get("arabic_script")])
        normalized.append(normalize_entry(
            source="murray",
            source_index=index,
            page=page,
            headword=entry.get("headword", ""),
            entry_type="word",
            english=entry.get("english"),
            arabic=arabic,
            forms=forms,
            pos=entry.get("pos", ""),
            category=first_non_empty(entry.get("categories")),
            categories=entry.get("categories", []),
            cognates=entry.get("cognates", []),
            usage_examples=entry.get("usage_examples", []),
            difficulty=entry.get("difficulty", ""),
            ipa=entry.get("ipa", ""),
            simple_roman=entry.get("simple_roman", ""),
            root=entry.get("root", ""),
            is_loanword=entry.get("is_loanword", False),
            loanword_source=entry.get("loanword_source", ""),
            is_archaic=entry.get("is_archaic", False),
            generated_example=entry.get("example_sentence", ""),
            raw_entry=entry,
        ))
    return normalized


def normalize_armbruster(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for index, entry in enumerate(entries, start=1):
        headword = clean_string(entry.get("headword"))
        normalized.append(normalize_entry(
            source="armbruster",
            source_index=index,
            page=entry.get("page_number"),
            headword=headword,
            normalized_headword=entry.get("headword_normalized", ""),
            entry_type="word",
            english=entry.get("english"),
            forms=[{"dialect": "D", "romanization": headword}] if headword else [],
            pos=entry.get("pos", ""),
            etymology=entry.get("etymology", ""),
            usage_examples=entry.get("usage_examples", []),
            inflections=entry.get("verb_forms", {}),
            screenshot=entry.get("source_screenshot"),
            raw_entry=entry,
        ))
    return normalized


def normalize_armbruster_llm(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for index, entry in enumerate(entries, start=1):
        headword = clean_string(entry.get("headword"))
        arabic = clean_list([entry.get("arabic_translation"), entry.get("arabic_script")])
        normalized.append(normalize_entry(
            source="armbruster",
            source_index=index,
            page=entry.get("_page"),
            headword=headword,
            normalized_headword=entry.get("headword_simple", ""),
            entry_type="word",
            english=entry.get("english"),
            arabic=arabic,
            forms=[{"dialect": "D", "romanization": headword}] if headword else [],
            pos=entry.get("pos", ""),
            category=first_non_empty(entry.get("categories")),
            categories=entry.get("categories", []),
            etymology=entry.get("etymology", ""),
            usage_examples=entry.get("usage_examples", []),
            inflections=entry.get("verb_forms", {}),
            difficulty=entry.get("difficulty", ""),
            ipa=entry.get("ipa", ""),
            is_loanword=entry.get("is_loanword", False),
            raw_entry={**entry, "_method": "llm-columns"},
        ))
    return normalized


def deterministic_armbruster_pages() -> set[int]:
    parsed_dir = OUTPUT_DIR / "armbruster" / "parsed"
    return {
        page_number_from_path(path)
        for path in parsed_dir.glob("page_*.json")
    }


def armbruster_llm_continuation_entries(deterministic_pages: set[int]) -> list[dict[str, Any]]:
    if not ARMBRUSTER_LLM_PATH.exists():
        return []
    entries = read_json(ARMBRUSTER_LLM_PATH).get("entries", [])
    return [
        entry for entry in entries
        if int(entry.get("_page") or 0) not in deterministic_pages
    ]


def normalize_entry(
    *,
    source: str,
    source_index: int,
    page: Any,
    headword: Any,
    entry_type: str,
    english: Any,
    forms: Any,
    raw_entry: dict[str, Any],
    normalized_headword: Any = "",
    arabic: Any = None,
    sudani_arabic: Any = None,
    pos: Any = "",
    category: Any = "",
    categories: Any = None,
    section: Any = "",
    etymology: Any = "",
    cognates: Any = None,
    usage_examples: Any = None,
    inflections: Any = None,
    difficulty: Any = "",
    ipa: Any = "",
    simple_roman: Any = "",
    root: Any = "",
    is_loanword: Any = None,
    loanword_source: Any = "",
    is_archaic: Any = None,
    generated_example: Any = "",
    screenshot: Any = None,
    proverb: dict[str, Any] | None = None,
) -> dict[str, Any]:
    page_number = int(page) if str(page or "").isdigit() else None
    cleaned_headword = clean_string(headword)
    normalized = clean_string(normalized_headword) or normalize_key(cleaned_headword)
    entry_id = f"{source}_{page_number or 0}_{source_index}"

    record: dict[str, Any] = {
        "id": entry_id,
        "entry_type": entry_type or "word",
        "source": source,
        "headword": cleaned_headword,
        "normalized_headword": normalized,
        "pos": clean_string(pos),
        "category": clean_string(category),
        "categories": clean_list(categories),
        "section": clean_string(section),
        "forms": [form for form in as_list(forms) if isinstance(form, dict) and any(form.values())],
        "english": clean_list(english),
        "arabic": clean_list(arabic),
        "sudani_arabic": clean_list(sudani_arabic),
        "inflections": inflections or {},
        "etymology": clean_string(etymology),
        "cognates": [item for item in as_list(cognates) if isinstance(item, dict)],
        "usage_examples": as_list(usage_examples),
        "difficulty": clean_string(difficulty),
        "ipa": clean_string(ipa),
        "simple_roman": clean_string(simple_roman),
        "root": clean_string(root),
        "is_loanword": is_loanword,
        "loanword_source": clean_string(loanword_source),
        "is_archaic": is_archaic,
        "generated_example": clean_string(generated_example),
        "sources": [source_ref(source, page_number, clean_string(screenshot))],
        "cross_refs": [],
        "raw_entry": raw_entry,
    }
    if proverb:
        record["proverb"] = {key: clean_string(value) for key, value in proverb.items() if clean_string(value)}

    return {key: value for key, value in record.items() if value not in ("", [], {}, None)}


def load_normalized_entries() -> list[dict[str, Any]]:
    sambaj = read_json(OUTPUT_DIR / "sambaj" / "sambaj_dictionary_v2.json").get("entries", [])
    satzinger = read_json(OUTPUT_DIR / "satzinger" / "satzinger_parsed.json").get("entries", [])
    proverbs = read_json(OUTPUT_DIR / "proverbs" / "proverbs_parsed.json").get("entries", [])
    armbruster = read_json(OUTPUT_DIR / "armbruster" / "parsed" / "armbruster_parsed.json").get("entries", [])
    deterministic_pages = deterministic_armbruster_pages()
    armbruster_llm = armbruster_llm_continuation_entries(deterministic_pages)

    normalized = []
    normalized.extend(normalize_sambaj(sambaj))
    normalized.extend(normalize_pass_through("satzinger", satzinger))
    normalized.extend(normalize_pass_through("proverbs", proverbs))
    normalized.extend(normalize_murray(load_murray_page_entries()))
    normalized.extend(normalize_armbruster(armbruster))
    normalized.extend(normalize_armbruster_llm(armbruster_llm))
    return normalized


def merge_corpus() -> dict[str, Any]:
    entries = load_normalized_entries()
    clusters_by_key: dict[str, list[str]] = defaultdict(list)

    for entry in entries:
        key = entry.get("normalized_headword") or normalize_key(entry.get("headword", ""))
        if not key:
            key = normalize_key(first_non_empty(entry.get("english")))
        if key:
            clusters_by_key[key].append(entry["id"])

    cluster_records = []
    entries_by_id = {entry["id"]: entry for entry in entries}
    for key, ids in sorted(clusters_by_key.items()):
        if len(ids) < 2:
            continue
        for entry_id in ids:
            entries_by_id[entry_id]["cross_refs"] = [other for other in ids if other != entry_id]
        sources = sorted({entries_by_id[entry_id]["source"] for entry_id in ids})
        cluster_records.append({
            "key": key,
            "entry_ids": ids,
            "sources": sources,
        })

    entry_type_counts = Counter(entry.get("entry_type", "word") for entry in entries)
    source_counts = Counter(entry.get("source", "") for entry in entries)
    corpus = {
        "metadata": {
            "version": "0.1.0",
            "generated_at": utc_now(),
            "sources": SOURCE_INFO,
            "stats": {
                "total_entries": len(entries),
                "source_counts": dict(source_counts),
                "entry_type_counts": dict(entry_type_counts),
                "clusters": len(cluster_records),
                "cross_referenced_entries": sum(1 for entry in entries if entry.get("cross_refs")),
            },
        },
        "entries": entries,
        "clusters": cluster_records,
    }
    write_json(UNIFIED_CORPUS_PATH, corpus)
    return corpus


def source_rank(source: str) -> int:
    return SOURCE_PRIORITY.get(source, 99)


def source_marker(entry: dict[str, Any]) -> dict[str, Any]:
    marker = {
        "source": entry.get("source", ""),
        "entry_id": entry.get("id", ""),
    }
    for ref in entry.get("sources", []):
        if isinstance(ref, dict) and ref.get("page"):
            marker["page"] = ref["page"]
            break
    return {key: value for key, value in marker.items() if value not in ("", None)}


def entry_summary(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_id": entry.get("id", ""),
        "source": entry.get("source", ""),
        "headword": entry.get("headword", ""),
        "pos": entry.get("pos", ""),
        "english": entry.get("english", []),
        "sources": entry.get("sources", []),
    }


def normalize_english_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).lower()
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def gloss_tokens(entry: dict[str, Any]) -> set[str]:
    tokens = set()
    for gloss in clean_list(entry.get("english")):
        for token in normalize_english_text(gloss).split():
            if len(token) >= 3 and token not in GLOSS_STOPWORDS:
                tokens.add(token)
    return tokens


def normalized_glosses(entry: dict[str, Any]) -> set[str]:
    return {
        normalized
        for gloss in clean_list(entry.get("english"))
        if (normalized := normalize_english_text(gloss))
    }


def sense_match(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any] | None:
    left_tokens = gloss_tokens(left)
    right_tokens = gloss_tokens(right)
    overlap = sorted(left_tokens & right_tokens)
    exact_glosses = sorted(normalized_glosses(left) & normalized_glosses(right))
    if not left_tokens or not right_tokens:
        return None

    coverage = len(overlap) / max(min(len(left_tokens), len(right_tokens)), 1)
    strong_overlap = len(overlap) >= 2
    rare_single_overlap = coverage >= 0.33 and any(len(token) >= 4 for token in overlap)
    exact_match = bool(exact_glosses)
    if not (exact_match or strong_overlap or rare_single_overlap):
        return None

    return {
        "left": left.get("id", ""),
        "right": right.get("id", ""),
        "overlap": overlap,
        "exact_glosses": exact_glosses,
        "score": round(coverage, 3),
    }


def connected_components(entries: list[dict[str, Any]]) -> tuple[list[list[dict[str, Any]]], dict[tuple[str, str], dict[str, Any]]]:
    edges: dict[str, set[str]] = defaultdict(set)
    evidence: dict[tuple[str, str], dict[str, Any]] = {}

    for index, left in enumerate(entries):
        for right in entries[index + 1:]:
            if left.get("source") == right.get("source"):
                continue
            match = sense_match(left, right)
            if not match:
                continue
            left_id = left["id"]
            right_id = right["id"]
            edges[left_id].add(right_id)
            edges[right_id].add(left_id)
            evidence[tuple(sorted([left_id, right_id]))] = match

    entries_by_id = {entry["id"]: entry for entry in entries}
    seen = set()
    components = []
    for entry_id in sorted(edges):
        if entry_id in seen:
            continue
        stack = [entry_id]
        component_ids = []
        seen.add(entry_id)
        while stack:
            current = stack.pop()
            component_ids.append(current)
            for next_id in edges[current]:
                if next_id in seen:
                    continue
                seen.add(next_id)
                stack.append(next_id)
        components.append([entries_by_id[item] for item in sorted(component_ids)])

    return components, evidence


def add_provenance_value(bucket: dict[str, dict[str, Any]], value: Any, entry: dict[str, Any]) -> None:
    cleaned = clean_string(value)
    if not cleaned:
        return
    key = normalize_english_text(cleaned) or normalize_key(cleaned)
    record = bucket.setdefault(key, {"value": cleaned, "sources": []})
    marker = source_marker(entry)
    if marker not in record["sources"]:
        record["sources"].append(marker)


def provenance_values(bucket: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    values = list(bucket.values())
    for item in values:
        item["sources"].sort(key=lambda source: (source_rank(source.get("source", "")), source.get("entry_id", "")))
    values.sort(key=lambda item: (-len({source["source"] for source in item["sources"]}), item["value"]))
    return values


def merge_list_property(entries: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    bucket: dict[str, dict[str, Any]] = {}
    for entry in entries:
        for value in clean_list(entry.get(field)):
            add_provenance_value(bucket, value, entry)
    return provenance_values(bucket)


def merge_string_property(entries: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    bucket: dict[str, dict[str, Any]] = {}
    for entry in entries:
        add_provenance_value(bucket, entry.get(field), entry)
    return provenance_values(bucket)


def merge_names(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bucket: dict[str, dict[str, Any]] = {}

    def add_name(value: Any, entry: dict[str, Any]) -> None:
        cleaned = clean_string(value)
        if not cleaned:
            return
        record = bucket.setdefault(cleaned, {
            "value": cleaned,
            "normalized": normalize_key(cleaned),
            "sources": [],
        })
        marker = source_marker(entry)
        if marker not in record["sources"]:
            record["sources"].append(marker)

    for entry in entries:
        add_name(entry.get("headword"), entry)
        for form in entry.get("forms", []):
            if not isinstance(form, dict):
                continue
            add_name(form.get("romanization"), entry)
            add_name(form.get("script"), entry)

    names = list(bucket.values())
    for name in names:
        name["sources"].sort(key=lambda source: (source_rank(source.get("source", "")), source.get("entry_id", "")))
    names.sort(key=lambda item: (source_rank(item["sources"][0].get("source", "")), item["normalized"], item["value"]))
    return names


def merge_forms(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    forms = []
    for entry in sorted(entries, key=lambda item: (source_rank(item.get("source", "")), item.get("id", ""))):
        for form in entry.get("forms", []):
            if not isinstance(form, dict):
                continue
            cleaned = {key: clean_string(value) for key, value in form.items() if clean_string(value)}
            if not cleaned:
                continue
            key = json.dumps(cleaned, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            forms.append({**cleaned, "source": source_marker(entry)})
    return forms


def merge_usage_examples(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    examples = []
    for entry in sorted(entries, key=lambda item: (source_rank(item.get("source", "")), item.get("id", ""))):
        for example in entry.get("usage_examples", []):
            if not isinstance(example, dict):
                continue
            cleaned = {key: clean_string(value) for key, value in example.items() if clean_string(value)}
            if not cleaned:
                continue
            key = json.dumps(cleaned, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            examples.append({**cleaned, "source": source_marker(entry)})
    return examples


def merge_inflections(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bucket: dict[str, dict[str, Any]] = {}
    for entry in entries:
        inflections = entry.get("inflections")
        if not isinstance(inflections, dict):
            continue
        for kind, value in inflections.items():
            cleaned = clean_string(value)
            if not cleaned:
                continue
            key = json.dumps({"kind": kind, "value": cleaned}, sort_keys=True, ensure_ascii=False)
            record = bucket.setdefault(key, {"kind": kind, "value": cleaned, "sources": []})
            marker = source_marker(entry)
            if marker not in record["sources"]:
                record["sources"].append(marker)
    return provenance_values(bucket)


def choose_canonical_headword(entries: list[dict[str, Any]]) -> str:
    def quality(entry: dict[str, Any]) -> tuple[int, int, int, int, str]:
        headword = entry.get("headword", "")
        affix_penalty = int(headword.startswith("-") or headword.endswith("-") or "..." in headword)
        diacritic_score = sum(1 for char in headword if ord(char) > 127)
        return (
            source_rank(entry.get("source", "")),
            affix_penalty,
            -diacritic_score,
            len(headword),
            headword,
        )

    return sorted(entries, key=quality)[0].get("headword", "")


def canonical_id_for(key: str, index: int, entries: list[dict[str, Any]]) -> str:
    tokens = Counter(token for entry in entries for token in gloss_tokens(entry))
    sense = "_".join(token for token, _ in tokens.most_common(3)) or "sense"
    return f"canonical_{key}_{index}_{sense}"


def canonical_confidence(entries: list[dict[str, Any]], matched_pairs: list[dict[str, Any]], shared_tokens: list[str]) -> str:
    if len({entry.get("source") for entry in entries}) >= 3:
        return "confirmed_3plus_sources"
    if any(match.get("exact_glosses") for match in matched_pairs):
        return "confirmed_exact_gloss"
    if len(shared_tokens) >= 2:
        return "confirmed_shared_glosses"
    return "probable_single_shared_token"


def build_canonical_word(
    key: str,
    index: int,
    entries: list[dict[str, Any]],
    evidence: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    entries = sorted(entries, key=lambda entry: (source_rank(entry.get("source", "")), entry.get("id", "")))
    ids = {entry["id"] for entry in entries}
    matched_pairs = [
        match for pair, match in evidence.items()
        if pair[0] in ids and pair[1] in ids
    ]
    shared_tokens = sorted({token for match in matched_pairs for token in match.get("overlap", [])})
    english_provenance = merge_list_property(entries, "english")

    return {
        "canonical_id": canonical_id_for(key, index, entries),
        "confidence": canonical_confidence(entries, matched_pairs, shared_tokens),
        "normalized_headword": key,
        "canonical_headword": choose_canonical_headword(entries),
        "names": merge_names(entries),
        "english": [item["value"] for item in english_provenance],
        "arabic": [item["value"] for item in merge_list_property(entries, "arabic")],
        "sudani_arabic": [item["value"] for item in merge_list_property(entries, "sudani_arabic")],
        "pos": [item["value"] for item in merge_string_property(entries, "pos")],
        "categories": [item["value"] for item in merge_list_property(entries, "categories")],
        "forms": merge_forms(entries),
        "usage_examples": merge_usage_examples(entries),
        "inflections": merge_inflections(entries),
        "etymology": merge_string_property(entries, "etymology"),
        "ipa": merge_string_property(entries, "ipa"),
        "root": merge_string_property(entries, "root"),
        "source_count": len({entry.get("source") for entry in entries}),
        "entry_count": len(entries),
        "source_entries": [entry_summary(entry) for entry in entries],
        "evidence": {
            "shared_gloss_tokens": shared_tokens,
            "matched_pairs": sorted(matched_pairs, key=lambda item: (-item["score"], item["left"], item["right"]))[:25],
        },
        "provenance": {
            "english": english_provenance,
            "arabic": merge_list_property(entries, "arabic"),
            "sudani_arabic": merge_list_property(entries, "sudani_arabic"),
            "pos": merge_string_property(entries, "pos"),
            "categories": merge_list_property(entries, "categories"),
        },
    }


def build_canonical_words(corpus: dict[str, Any] | None = None) -> dict[str, Any]:
    corpus = corpus or (read_json(UNIFIED_CORPUS_PATH) if UNIFIED_CORPUS_PATH.exists() else merge_corpus())
    entries = [
        entry for entry in corpus.get("entries", [])
        if entry.get("entry_type") == "word"
        and entry.get("normalized_headword")
        and entry.get("english")
    ]

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        grouped[entry["normalized_headword"]].append(entry)

    canonical_words = []
    needs_review = []
    rejected_candidates = []
    multi_source_group_count = 0
    used_entry_ids = set()

    for key, group_entries in sorted(grouped.items()):
        sources = sorted({entry.get("source", "") for entry in group_entries if entry.get("source")})
        if len(sources) < 2:
            continue
        multi_source_group_count += 1

        components, evidence = connected_components(group_entries)
        canonical_components = [
            component for component in components
            if len({entry.get("source") for entry in component}) >= 2
        ]

        if len(key) < 3:
            if canonical_components:
                needs_review.append({
                    "key": key,
                    "reason": "short_normalized_headword",
                    "sources": sources,
                    "entries": [entry_summary(entry) for entry in group_entries],
                })
            continue

        if not canonical_components:
            needs_review.append({
                "key": key,
                "reason": "multi_source_headword_without_clear_sense_overlap",
                "sources": sources,
                "entries": [entry_summary(entry) for entry in group_entries],
            })
            continue

        for index, component in enumerate(canonical_components, start=1):
            canonical_words.append(build_canonical_word(key, index, component, evidence))
            used_entry_ids.update(entry["id"] for entry in component)

        unmatched = [entry for entry in group_entries if entry["id"] not in used_entry_ids]
        if unmatched:
            rejected_candidates.append({
                "key": key,
                "reason": "same_headword_but_unmatched_sense_or_single_source_component",
                "sources": sorted({entry.get("source", "") for entry in unmatched if entry.get("source")}),
                "entries": [entry_summary(entry) for entry in unmatched],
            })

    canonical_words.sort(key=lambda item: (item["normalized_headword"], item["canonical_id"]))
    result = {
        "metadata": {
            "version": "0.1.0",
            "generated_at": utc_now(),
            "minimum_sources": 2,
            "match_strategy": "normalized_headword + source-count + English-gloss sense overlap",
            "stats": {
                "input_entries": len(entries),
                "multi_source_headword_groups": multi_source_group_count,
                "canonical_words": len(canonical_words),
                "source_entries_used": len(used_entry_ids),
                "needs_review": len(needs_review),
                "rejected_candidates": len(rejected_candidates),
            },
        },
        "canonical_words": canonical_words,
        "needs_review": needs_review,
        "rejected_candidates": rejected_candidates,
    }
    result["metadata"]["stats"]["confidence_counts"] = dict(Counter(
        word["confidence"] for word in canonical_words
    ))
    write_json(CANONICAL_WORDS_PATH, result)
    return result


def build_app_datasets() -> dict[str, Any]:
    corpus = read_json(UNIFIED_CORPUS_PATH) if UNIFIED_CORPUS_PATH.exists() else merge_corpus()
    entries = corpus["entries"]
    entries_by_id = {entry["id"]: entry for entry in entries}

    vocabulary = []
    for entry in entries:
        if entry.get("entry_type") == "proverb" or not entry.get("english"):
            continue
        vocabulary.append({
            "id": entry["id"],
            "headword": entry.get("headword", ""),
            "normalized_headword": entry.get("normalized_headword", ""),
            "forms": entry.get("forms", []),
            "english": entry.get("english", []),
            "arabic": entry.get("arabic", []),
            "source": entry.get("source", ""),
            "category": entry.get("category", ""),
            "categories": entry.get("categories", []),
            "difficulty": entry.get("difficulty", ""),
        })

    dialect_comparisons = []
    for cluster in corpus.get("clusters", []):
        cluster_entries = [entries_by_id[entry_id] for entry_id in cluster["entry_ids"]]
        dialects = sorted({
            form.get("dialect", "")
            for entry in cluster_entries
            for form in entry.get("forms", [])
            if form.get("dialect")
        })
        if len(dialects) < 2:
            continue
        dialect_comparisons.append({
            "key": cluster["key"],
            "dialects": dialects,
            "sources": cluster["sources"],
            "entries": [{
                "id": entry["id"],
                "source": entry["source"],
                "headword": entry.get("headword", ""),
                "forms": entry.get("forms", []),
                "english": entry.get("english", []),
            } for entry in cluster_entries],
        })

    proverbs = []
    for entry in entries:
        if entry.get("entry_type") != "proverb":
            continue
        proverbs.append({
            "id": entry["id"],
            "text": entry.get("proverb", {}).get("text", entry.get("headword", "")),
            "transliteration": entry.get("proverb", {}).get("transliteration", ""),
            "literal": entry.get("proverb", {}).get("literal", ""),
            "meaning": entry.get("proverb", {}).get("meaning", ""),
            "script": entry.get("proverb", {}).get("script", ""),
        })

    flashcards = [{
        "id": item["id"],
        "prompt": item["headword"],
        "answer": "; ".join(item["english"][:3]),
        "source": item["source"],
        "difficulty": item.get("difficulty", ""),
        "category": item.get("category", ""),
    } for item in vocabulary if item.get("headword") and item.get("english")]

    search_index = []
    for entry in entries:
        tokens = [entry.get("headword", ""), entry.get("normalized_headword", "")]
        tokens.extend(entry.get("english", []))
        tokens.extend(entry.get("arabic", []))
        for form in entry.get("forms", []):
            tokens.extend([form.get("romanization", ""), form.get("script", "")])
        search_index.append({
            "id": entry["id"],
            "entry_type": entry.get("entry_type", ""),
            "source": entry.get("source", ""),
            "headword": entry.get("headword", ""),
            "normalized_headword": entry.get("normalized_headword", ""),
            "tokens": clean_list(tokens),
        })

    datasets = {
        "vocabulary_core": vocabulary,
        "dialect_comparison": dialect_comparisons,
        "proverb_sentences": proverbs,
        "flashcards": flashcards,
        "search_index": search_index,
    }
    for name, data in datasets.items():
        write_json(APP_DIR / f"{name}.json", data)

    summary = {
        "generated_at": utc_now(),
        "datasets": {name: len(data) for name, data in datasets.items()},
    }
    write_json(APP_DIR / "manifest.json", summary)
    return summary


def run_all() -> dict[str, Any]:
    murray = rebuild_murray_canonical()
    manifest = build_manifest()
    qa = run_qa()
    corpus = merge_corpus()
    canonical = build_canonical_words(corpus)
    app = build_app_datasets()
    return {
        "murray": murray,
        "manifest_sources": {
            source: data["entry_count"] for source, data in manifest["sources"].items()
        },
        "qa": qa["summary"],
        "corpus": corpus["metadata"]["stats"],
        "canonical": canonical["metadata"]["stats"],
        "app": app["datasets"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the unified Nubian corpus.")
    parser.add_argument(
        "command",
        choices=["refresh-murray", "manifest", "qa", "merge", "canonical", "app-datasets", "all"],
        help="Pipeline step to run.",
    )
    args = parser.parse_args()

    if args.command == "refresh-murray":
        result = rebuild_murray_canonical()
    elif args.command == "manifest":
        result = build_manifest()
    elif args.command == "qa":
        result = run_qa()
    elif args.command == "merge":
        result = merge_corpus()["metadata"]["stats"]
    elif args.command == "canonical":
        result = build_canonical_words()["metadata"]["stats"]
    elif args.command == "app-datasets":
        result = build_app_datasets()
    else:
        result = run_all()

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
