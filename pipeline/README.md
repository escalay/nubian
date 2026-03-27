# Dongolese Nubian Lexicon Digitization Pipeline

Transforms Armbruster's *Dongolese Nubian: A Lexicon* (CUP, 1965) from a scanned PDF
into structured, machine-readable JSON.

## PDF Page Map

| Pages (PDF) | Content |
|---|---|
| 1-7 | Title, copyright, dedication |
| 8 | Preface |
| 9-10 | Abbreviations, Signs and Symbols |
| 11-17 | Bibliography of proper names and publications |
| **18-222** | **NUBIAN-ENGLISH dictionary** (the primary target) |
| **223-286** | **ENGLISH-NUBIAN dictionary** |
| 287-291 | Blank pages, back cover |

## Architecture

```
Stage 1: OCR (stage1_ocr.py)
  Scanned PDF pages  ──►  Chandra OCR on M4 Max GPU  ──►  HTML per page
                                                          (preserves <b>, <i> tags)

Stage 2: Parse (stage2_parse.py)
  HTML per page  ──►  Rule-based parser  ──►  Structured JSON entries
                      (detects bold headwords,
                       italic glosses, §-refs,
                       [etymology], (variants),
                       ˘ connectors, etc.)
```

## Quick Start

```bash
cd pipeline
bash setup.sh            # Creates venv, installs chandra-ocr[hf] + PyTorch

source .venv/bin/activate

# Test on 3 pages first
python stage1_ocr.py \
  -i "../books/Dongolese Nubian_ A Lexicon - Charles Hubert Armbruster (1).pdf" \
  -o ./ocr_output \
  --page-range 18-20

# Parse into structured JSON
python stage2_parse.py \
  -i ./ocr_output \
  -o ./lexicon_test.json \
  --validate --pretty
```

## Full Run

```bash
# Nubian-English (205 pages, the richly structured section)
python stage1_ocr.py \
  -i "../books/Dongolese Nubian_ A Lexicon - Charles Hubert Armbruster (1).pdf" \
  -o ./ocr_output \
  --page-range 18-222 \
  --resume

# English-Nubian (64 pages, simpler structure)
python stage1_ocr.py \
  -i "../books/Dongolese Nubian_ A Lexicon - Charles Hubert Armbruster (1).pdf" \
  -o ./ocr_output \
  --page-range 223-286 \
  --resume

# Parse everything
python stage2_parse.py \
  -i ./ocr_output \
  -o ./armbruster_lexicon.json \
  --validate --pretty
```

The `--resume` flag skips pages that already have output files, so you can
interrupt and restart safely.

## Output Schema

Each entry in the JSON output contains:

```json
{
  "headword": "bér",
  "parent_headword": null,
  "variant_forms": [],
  "part_of_speech": "v.i.",
  "grammar_references": ["§3890ff."],
  "definitions": [{"sense_label": "a", "gloss": "get sated, have enough"}],
  "etymology": "[RSN, §129]",
  "possessive_paradigm": null,
  "inflections": {"perf": "fi̥˘bérkorī˘"},
  "compounds": [],
  "usage_examples": [
    {
      "nubian": "íngrīg˘éttará?",
      "english": "shall I bring the sweet?",
      "morphophonological_notes": null
    }
  ],
  "cultural_notes": null,
  "cross_references": ["§5369b"],
  "page_number": 34,
  "raw_text": "..."
}
```

## Requirements

- Python 3.10+
- Apple Silicon Mac (M4 Max recommended) or NVIDIA GPU
- ~8GB disk for the Chandra model weights (downloaded on first run)
- The lexicon PDF in `../books/`

## Known Considerations

- **˘ connector symbol**: Chandra should preserve this, but validate on the first
  few pages. If it's being dropped, the Stage 2 parser has fallback detection
  for the combining breve (U+0306) and other similar characters.
- **Arabic script in etymologies**: Chandra handles 90+ languages including Arabic.
  Check the `[< ...]` etymology brackets in the output.
- **MPS on Apple Silicon**: Chandra uses `device_map="auto"` which should pick up
  MPS. If it doesn't, set `TORCH_DEVICE=mps` in a `local.env` file in the
  pipeline directory.
- **Memory**: The Chandra-OCR-2 model in bfloat16 needs ~8-16GB. The M4 Max's
  unified memory (36-128GB) is more than sufficient.
