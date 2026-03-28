"""
JSON schema and data model for the Sambaj Nubian Dictionary.

القاموس النوبي — يوسف سمباج
Nubian-Arabic-English trilingual dictionary

Book structure:
  - 148 pages total, dictionary on pages 20-~139
  - 1,827 entries across A-Y sections
  - 6 columns per entry:
    1. English gloss
    2. Kenzi-Dongolawi romanization (K-D)
    3. Fadija-Mahas romanization (F-M)
    4. Arabic translation (العربية)
    5. Kenzi-Dongolawi script (الكنزية الدنقلاوية)
    6. Fadija script (الفديجا المحسية)
"""

import re
from dataclasses import dataclass, field, asdict
from typing import Optional


def _split_on(value: str, pattern: str) -> list[str]:
    if not value or value in ("-", "–", "—"):
        return []
    parts = re.split(pattern, value)
    return [p.strip() for p in parts if p.strip() and p.strip() not in ("-", "–", "—")]


def split_comma(value: str) -> list[str]:
    """Split on ',' (with optional space) — used for English and romanization columns."""
    return _split_on(value, r',\s*')


def split_hyphen(value: str) -> list[str]:
    """Split on ' - ' or ' – ' — used for Arabic and Nubian script columns."""
    return _split_on(value, r'\s+[-–]\s+')


# Keep backward-compat alias
split_variants = split_comma


@dataclass
class DictionaryEntry:
    english: list[str] = field(default_factory=list)
    kenzi_dongolawi_roman: list[str] = field(default_factory=list)      # In (K-D) column
    fadija_mahas_roman: list[str] = field(default_factory=list)         # In (F-M) column
    arabic: list[str] = field(default_factory=list)                     # العربية
    kenzi_dongolawi_script: list[str] = field(default_factory=list)     # الكنزية الدنقلاوية
    fadija_script: list[str] = field(default_factory=list)              # الفديجا المحسية
    letter_section: str = ""            # A, B, C, ... Y
    page_number: Optional[int] = None
    source_page_image: Optional[str] = None  # path to page screenshot

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v}


# The full JSON output schema
OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Sambaj Nubian Dictionary",
    "description": "القاموس النوبي — يوسف سمباج (Nubian-Arabic-English)",
    "type": "object",
    "properties": {
        "metadata": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "title_ar": {"type": "string"},
                "author": {"type": "string"},
                "author_ar": {"type": "string"},
                "publisher": {"type": "string"},
                "languages": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "dialects": {
                    "type": "object",
                    "properties": {
                        "kenzi_dongolawi": {"type": "string", "description": "Nile Nubian, Kenzi-Dongolawi dialect group"},
                        "fadija_mahas": {"type": "string", "description": "Nile Nubian, Fadija-Mahas dialect group"},
                    },
                },
                "total_entries": {"type": "integer"},
                "pipeline_version": {"type": "string"},
            },
        },
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "english": {"type": "array", "items": {"type": "string"}, "description": "English glosses"},
                    "kenzi_dongolawi_roman": {"type": "array", "items": {"type": "string"}, "description": "Kenzi-Dongolawi romanization variants (K-D column)"},
                    "fadija_mahas_roman": {"type": "array", "items": {"type": "string"}, "description": "Fadija-Mahas romanization variants (F-M column)"},
                    "arabic": {"type": "array", "items": {"type": "string"}, "description": "Arabic translations (العربية)"},
                    "kenzi_dongolawi_script": {"type": "array", "items": {"type": "string"}, "description": "Kenzi-Dongolawi in modified Arabic script (الكنزية الدنقلاوية)"},
                    "fadija_script": {"type": "array", "items": {"type": "string"}, "description": "Fadija in modified Arabic script (الفديجا المحسية)"},
                    "letter_section": {"type": "string", "description": "Alphabetical section (A-Y)"},
                    "page_number": {"type": "integer", "description": "Source PDF page number"},
                    "source_page_image": {"type": "string", "description": "Path to page screenshot PNG"},
                },
                "required": ["english"],
            },
        },
    },
    "required": ["metadata", "entries"],
}

METADATA = {
    "title": "The Nubian Dictionary",
    "title_ar": "القاموس النوبي",
    "author": "Youssef Sambaj",
    "author_ar": "يوسف سمباج",
    "publisher": "مكتبة الشروق",
    "languages": ["Nubian (Nile Nubian)", "Arabic", "English"],
    "dialects": {
        "kenzi_dongolawi": "Kenzi-Dongolawi (K-D) — southern dialect group",
        "fadija_mahas": "Fadija-Mahas (F-M) — northern dialect group",
    },
    "pipeline_version": "0.1.0",
}
