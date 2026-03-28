"""
Unified Nubian Lexicographic Schema.

All book-specific parsers output entries conforming to this schema.
The merger stage cross-references entries across sources by headword + dialect.

Supports:
  - Dictionary entries (word-level)
  - Proverbs (sentence-level, with individual word cross-refs)
  - Comparative cognates (Arabic, Hamitic, Semitic, Coptic, Old Nubian)
  - Multi-dialect coverage (Kenzi, Dongolawi, Mahas/Fadijja, Dairawi, Kordofan, Midob)
  - Topic categories for language learning
  - Source provenance with page numbers and screenshot paths
"""

from dataclasses import dataclass, field
from typing import Optional
import json


# ─────────────────────────────────────────────────────────────
# Dialect Codes
# ─────────────────────────────────────────────────────────────

DIALECTS = {
    "K": "Kenzi",
    "D": "Dongolawi",
    "M": "Mahas",
    "F": "Fadijja",
    "KD": "Kenzi-Dongolawi",
    "FM": "Fadijja-Mahas",
    "KDM": "Kenzi-Dongolawi-Mahas",
    "Dai": "Dairawi",
    "Kdr": "Kordofan",
    "Mid": "Midob",
    "ON": "Old Nubian",
}


# ─────────────────────────────────────────────────────────────
# Entry Types
# ─────────────────────────────────────────────────────────────

ENTRY_TYPES = {
    "word": "Dictionary headword",
    "phrase": "Multi-word expression or compound",
    "proverb": "Proverb or saying with cultural context",
    "greeting": "Greeting or salutation formula",
    "pronoun": "Personal/demonstrative pronoun",
    "numeral": "Number",
}


# ─────────────────────────────────────────────────────────────
# Source Provenance
# ─────────────────────────────────────────────────────────────

SOURCES = {
    "sambaj": {
        "short": "Sambaj",
        "title": "The Nubian Dictionary (القاموس النوبي)",
        "author": "Youssef Sambaj",
        "year": None,
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


# ─────────────────────────────────────────────────────────────
# Data Model
# ─────────────────────────────────────────────────────────────

@dataclass
class SourceRef:
    """Where this entry came from."""
    book: str                          # key from SOURCES
    page: Optional[int] = None
    screenshot: Optional[str] = None   # relative path to page image

    def to_dict(self) -> dict:
        d = {"book": self.book}
        if self.page: d["page"] = self.page
        if self.screenshot: d["screenshot"] = self.screenshot
        return d


@dataclass
class DialectForm:
    """A word form in a specific dialect."""
    dialect: str                       # code from DIALECTS
    romanization: str = ""             # Latin script form
    script: str = ""                   # Arabic/Nubian script form
    tone: str = ""                     # tone markings if available
    plural: str = ""                   # plural form
    notes: str = ""                    # e.g., "only in compounds"

    def to_dict(self) -> dict:
        return {k: v for k, v in {
            "dialect": self.dialect,
            "romanization": self.romanization,
            "script": self.script,
            "tone": self.tone,
            "plural": self.plural,
            "notes": self.notes,
        }.items() if v}


@dataclass
class Cognate:
    """A comparative cognate in another language."""
    language: str                      # e.g., "Arabic", "Coptic", "Hamitic", "Semitic", "Old Nubian"
    form: str = ""
    meaning: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in {
            "language": self.language,
            "form": self.form,
            "meaning": self.meaning,
        }.items() if v}


@dataclass
class NubianEntry:
    """A single entry in the unified Nubian corpus."""

    # Identity
    id: str = ""                       # generated: "{source}_{page}_{index}"
    entry_type: str = "word"           # from ENTRY_TYPES
    headword: str = ""                 # primary romanized form
    headword_script: str = ""          # primary script form (Arabic or Old Nubian)

    # Classification
    part_of_speech: str = ""           # n., v.t., v.i., adj., adv., etc.
    category: str = ""                 # topic category for learning: "greeting", "animal", "food", etc.
    letter_section: str = ""           # A-Z

    # Dialect forms
    forms: list = field(default_factory=list)  # list of DialectForm

    # Translations
    english: list = field(default_factory=list)         # English glosses
    arabic: list = field(default_factory=list)          # Standard Arabic translations
    sudani_arabic: list = field(default_factory=list)   # Sudani Arabic forms

    # Morphology
    inflections: dict = field(default_factory=dict)     # nested verb forms
    compounds: list = field(default_factory=list)       # derived/compound forms

    # Etymology & Cognates
    etymology: str = ""
    cognates: list = field(default_factory=list)        # list of Cognate

    # Usage
    usage_examples: list = field(default_factory=list)  # list of {"nubian": str, "english": str}

    # Proverb-specific
    proverb_text: str = ""             # full proverb in Nubian
    proverb_transliteration: str = ""  # Latin transliteration
    proverb_literal: str = ""          # literal English translation
    proverb_meaning: str = ""          # cultural/contextual meaning
    proverb_nubian_script: str = ""    # Old Nubian script form

    # Provenance
    sources: list = field(default_factory=list)  # list of SourceRef
    cross_refs: list = field(default_factory=list)  # IDs of related entries in other books
    quality_warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {}
        if self.id: d["id"] = self.id
        d["entry_type"] = self.entry_type
        if self.headword: d["headword"] = self.headword
        if self.headword_script: d["headword_script"] = self.headword_script
        if self.part_of_speech: d["pos"] = self.part_of_speech
        if self.category: d["category"] = self.category
        if self.letter_section: d["section"] = self.letter_section

        if self.forms:
            d["forms"] = [f.to_dict() for f in self.forms]
        if self.english: d["english"] = self.english
        if self.arabic: d["arabic"] = self.arabic
        if self.sudani_arabic: d["sudani_arabic"] = self.sudani_arabic

        if self.inflections: d["inflections"] = self.inflections
        if self.compounds: d["compounds"] = self.compounds
        if self.etymology: d["etymology"] = self.etymology
        if self.cognates:
            d["cognates"] = [c.to_dict() for c in self.cognates]
        if self.usage_examples: d["usage_examples"] = self.usage_examples

        # Proverb fields
        if self.proverb_text: d["proverb_text"] = self.proverb_text
        if self.proverb_transliteration: d["proverb_transliteration"] = self.proverb_transliteration
        if self.proverb_literal: d["proverb_literal"] = self.proverb_literal
        if self.proverb_meaning: d["proverb_meaning"] = self.proverb_meaning
        if self.proverb_nubian_script: d["proverb_nubian_script"] = self.proverb_nubian_script

        if self.sources:
            d["sources"] = [s.to_dict() for s in self.sources]
        if self.cross_refs: d["cross_refs"] = self.cross_refs
        if self.quality_warnings: d["quality_warnings"] = self.quality_warnings

        return d


# ─────────────────────────────────────────────────────────────
# Corpus Output Schema
# ─────────────────────────────────────────────────────────────

CORPUS_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Unified Nubian Lexicographic Corpus",
    "type": "object",
    "properties": {
        "metadata": {
            "type": "object",
            "properties": {
                "version": {"type": "string"},
                "sources": {"type": "object"},
                "dialects": {"type": "object"},
                "stats": {
                    "type": "object",
                    "properties": {
                        "total_entries": {"type": "integer"},
                        "words": {"type": "integer"},
                        "proverbs": {"type": "integer"},
                        "phrases": {"type": "integer"},
                        "unique_headwords": {"type": "integer"},
                        "cross_referenced": {"type": "integer"},
                    },
                },
            },
        },
        "entries": {
            "type": "array",
            "items": {"type": "object"},
        },
    },
}


def save_entries(entries: list[NubianEntry], path: str, source_key: str, extra_meta: dict = None):
    """Save a list of NubianEntry objects to JSON with metadata."""
    meta = {
        "source": SOURCES[source_key],
        "total_entries": len(entries),
        "entry_types": {},
    }
    for e in entries:
        meta["entry_types"][e.entry_type] = meta["entry_types"].get(e.entry_type, 0) + 1

    if extra_meta:
        meta.update(extra_meta)

    data = {
        "metadata": meta,
        "entries": [e.to_dict() for e in entries],
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"  Saved {len(entries)} entries to {path}")
