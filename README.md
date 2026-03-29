# Nubian Lexicographic Corpus

Digitizing the Nubian language from scanned academic dictionaries into structured, machine-readable JSON — building the foundation for a Nubian language learning app.

## Why This Exists

Nubian (Nile Nubian) is an endangered language family spoken along the Nile in Sudan and southern Egypt. The richest documentation exists only in century-old printed dictionaries — locked in scanned PDFs that no search engine, app, or AI can read.

This project extracts, structures, and cross-references vocabulary from five major Nubian reference works, covering multiple dialects (Kenzi, Dongolawi, Mahas, Fadijja, Dairawi, Midob) with translations in English, Arabic, and Sudani Arabic.

The goal: a unified corpus that can power a Duolingo-style language learning app for Nubian.

## What's Inside

### Source Books

| Book | Author | Year | Dialect(s) | Content |
|---|---|---|---|---|
| **Dongolese Nubian: A Lexicon** | C.H. Armbruster | 1965 | Dongolawi | ~8,000 headwords with verb paradigms, usage examples, etymology |
| **An English-Nubian Comparative Dictionary** | G.W. Murray | 1923 | K, M, D, KD, KDM, Dai, Mid | ~3,500 headwords with comparative cognates across Arabic, Hamitic, Semitic, Nilotic |
| **The Nubian Dictionary (القاموس النوبي)** | Youssef Sambaj | — | Kenzi-Dongolawi, Fadijja-Mahas | 1,827 entries, trilingual (Nubian-Arabic-English) |
| **Short Archaeological Wordlist** | Helmut Satzinger | 2018 | Nobiin (Mahas) | 241 entries with Sudani Arabic, organized by topic |
| **Nubian Proverbs (Fadijja/Mahas)** | Maher Habbob | 2020 | Fadijja-Mahas | 500 proverbs with Old Nubian script, transliteration, cultural context |

### Extracted Data

| Source | Entries | Method | Quality |
|---|---|---|---|
| Murray 1923 | **3,517** | LLM canonical (Gemini 3 Flash) | 100% clean, 18 fields per entry |
| Armbruster 1965 | **3,436** (partial) | Column OCR + deterministic parser | 89% with English, ˘ preserved |
| Sambaj | **1,776** | OCR + enrichment pipeline | 98.5% quality score |
| Proverbs | **500** | OCR + proverb parser | 100% coverage |
| Satzinger | **241** | OCR + topic parser | 100% with categories |
| **Total** | **9,470 + 500 proverbs** | | |

## Architecture

Three extraction pipelines, each suited to different source material:

### 1. OCR Pipeline (`pipeline-api/`)

Uses the [Datalab API](https://www.datalab.to) to convert scanned pages to HTML, then deterministic regex parsers extract structured entries.

**Best for:** Tabular dictionaries (Sambaj), clean born-digital PDFs (Satzinger, Proverbs), and column-split dense dictionaries (Armbruster).

**Key innovation:** Armbruster's 3-column layout is split into individual column images at 4x resolution using per-page gutter detection (pixel density analysis). The OCR then detects the ˘ connector symbol as `<sub>⌣</sub>` — something it misses on full-page images.

### 2. LLM Vision Pipeline (`pipeline-llm/`)

Uses [funcai](https://github.com/AbdallahAHO/funcai) + Google Gemini 3 Flash via OpenRouter to read page screenshots directly with structured Zod schemas.

**Best for:** Murray's comparative dictionary (two-column layout where OCR merges columns), and maximum-value extraction with LLM-generated enrichments.

**Three approaches, each building on the previous:**
- **Plain vision** — send image, get entries
- **Hybrid** — image + OCR HTML as structural hint + thinking mode
- **Canonical** — hybrid + LLM-generated Arabic translations, IPA, categories, difficulty levels, example sentences (18 fields per entry)

### 3. Local GPU Pipeline (`pipeline/`)

Original prototype using [Chandra OCR](https://huggingface.co/datalab-to/chandra-ocr-2) on Apple Silicon. Superseded by the API and LLM approaches but kept for reference.

## Output Structure

Each book has its own output folder with every pipeline stage preserved separately:

```
output/
├── sambaj/
│   ├── html/                          OCR HTML per page (cached)
│   ├── screenshots/                   Page PNGs for visual reference
│   ├── sambaj_dictionary.json         Raw parsed
│   ├── sambaj_dictionary_clean.json   Manual fixes applied
│   └── sambaj_dictionary_v2.json      Enriched (verb groups, typos, sections)
├── satzinger/
│   ├── html/
│   └── satzinger_parsed.json          241 categorized entries
├── proverbs/
│   ├── html/
│   └── proverbs_parsed.json           500 proverbs
├── murray/
│   ├── html/                          189 pages OCR'd
│   ├── screenshots/                   189 page PNGs
│   ├── murray_parsed.json             OCR v1 baseline
│   ├── murray_parsed_v7.json          OCR v7 (cleaned)
│   ├── llm-canonical/                 Per-page canonical JSONs
│   └── murray_canonical.json          3,517 enriched entries
└── armbruster/
    ├── columns/                       615 column PNGs (4x resolution)
    ├── ocr-columns/                   256 column HTMLs (pages 18-103)
    ├── parsed/                        Per-page JSONs + combined
    ├── screenshots/                   205 full-page PNGs
    └── llm-columns/                   Per-page LLM extractions
```

## The ˘ Problem (and How We Solved It)

Armbruster's lexicon uses a special **˘** (breve) symbol as a morpheme juncture marker — it's linguistically significant but visually tiny (~1px).

| Approach | Detects ˘? |
|---|---|
| Full-page OCR (Datalab API) | No — reads it as hyphen or drops it |
| Full-page OCR (Chandra local) | No — too small at page resolution |
| Column-split OCR (4x resolution) | **Yes** — detects as `<sub>⌣</sub>` |
| LLM vision (Gemini) | **Yes** — reads it directly from image |

The solution: render at 4x resolution, split into columns, then the OCR can see the ˘. The deterministic parser normalizes `<sub>⌣</sub>` → `˘` and collapses whitespace to attach it to adjacent words.

## Schema

All books feed into a unified `NubianEntry` schema (`pipeline-api/unified_schema.py`) that captures:

- **Headword** with diacritics and ˘ connectors
- **Dialect forms** (which dialects the word appears in, with per-dialect romanization)
- **English meanings** (clean array of definitions)
- **Arabic translation** + script
- **Part of speech**
- **Verb paradigms** (present, perfect, imperative, participles)
- **Usage examples** (Nubian sentence + English translation)
- **Etymology** (often with Arabic, Greek, or Coptic origins)
- **Comparative cognates** (Arabic, Hamitic, Semitic, Nilotic — Murray only)
- **Topic categories** (animal, food, body, family, nature, etc.)
- **Difficulty level** (beginner, intermediate, advanced)
- **Source provenance** (book, page, screenshot path)

## How to Continue

### Finish Armbruster (pages 104-222)

The column images are ready — just need OCR or LLM processing:

```bash
# Option A: Top up Datalab credits ($2-3), then:
cd pipeline-api
python armbruster_pipeline.py ocr --page-range 104-222
python armbruster_pipeline.py parse --page-range 18-222

# Option B: Use LLM (no Datalab needed, uses OpenRouter):
cd pipeline-llm
npx tsx src/armbruster-columns.ts --pages 104-222
```

### Build the Unified Corpus

Merge all 5 books into one cross-referenced JSON:

```bash
cd pipeline-api
python unified_corpus_merger.py  # (to be built)
```

### Build the Language Learning App

With ~14,000 structured entries across all dialects, you have enough data to build:
- Flashcard vocabulary trainer
- Multiple choice quizzes
- Sentence building exercises (500 proverbs as sentence corpus)
- Dialect comparison tools
- Progressive difficulty levels (beginner → advanced)

## Tech Stack

- **Python 3.14** — OCR pipelines, parsers, data processing
- **TypeScript + funcai** — LLM vision extraction with structured Zod schemas
- **Datalab API** — OCR service (converts scanned PDFs to HTML)
- **Google Gemini 3 Flash** — Vision LLM for canonical extraction (via OpenRouter)
- **pypdfium2** — PDF rendering to PNG at arbitrary scale
- **NumPy + Pillow** — Column gutter detection via pixel density analysis
