/**
 * Canonical extraction for Armbruster's Dongolese Nubian: A Lexicon (1965).
 *
 * This is the most complex dictionary — dense entries with:
 *   - ˘ connector symbol (morpheme juncture)
 *   - Grammar section references (§§)
 *   - Full verb conjugation paradigms
 *   - Possessive pronoun paradigms
 *   - Usage example sentences
 *   - Etymology with Arabic script
 *   - Compound/derived forms
 *
 * Two sections:
 *   - Nubian-English: pages 18-222 (primary target)
 *   - English-Nubian: pages 223-286
 *
 * Run:
 *   npx tsx src/armbruster-canonical.ts --pages 18-22   # test
 *   npx tsx src/armbruster-canonical.ts --pages 18-222  # full Nubian-English
 */

import { createAiFn } from 'funcai';
import { openrouter } from 'funcai/providers/openrouter';
import { z } from 'zod';
import fs from 'fs';
import path from 'path';

const OPENROUTER_API_KEY = process.env.OPENROUTER_API_KEY;

const ai = createAiFn({
  provider: openrouter({ apiKey: OPENROUTER_API_KEY }),
});

const PDF_PATH = path.resolve('../books/Dongolese Nubian_ A Lexicon - Charles Hubert Armbruster (1).pdf');
const OUTPUT_DIR = path.resolve('../output/armbruster/llm-canonical');
const SCREENSHOTS_DIR = path.resolve('../output/armbruster/screenshots');
const HTML_DIR = path.resolve('../output/armbruster/html');

// ─────────────────────────────────────────────────────────────
// Schema — Armbruster-specific
// ─────────────────────────────────────────────────────────────

const VerbParadigm = z.object({
  present: z.string().optional().describe('Present tense form'),
  perfect: z.string().optional().describe('Perfect/past tense form'),
  imperative: z.string().optional().describe('Imperative form'),
  participle_present: z.string().optional().describe('Present participle'),
  participle_past: z.string().optional().describe('Past participle'),
});

const PossessiveParadigm = z.object({
  first_sg: z.string().optional().describe('1sg: my (ánn-)'),
  second_sg: z.string().optional().describe('2sg: your (énn-)'),
  third_sg: z.string().optional().describe('3sg: his/her (ténn-)'),
  first_pl: z.string().optional().describe('1pl: our (ánn-)'),
  second_pl: z.string().optional().describe('2pl: your (inn-)'),
  third_pl: z.string().optional().describe('3pl: their (tinn-)'),
});

const UsageExample = z.object({
  nubian: z.string().describe('Nubian sentence (preserve ˘ connectors!)'),
  english: z.string().describe('English translation'),
});

const CompoundForm = z.object({
  form: z.string().describe('The compound/derived form (preserve ˘!)'),
  meaning: z.string().describe('English meaning'),
  pos: z.string().optional().describe('Part of speech if given'),
  grammar_ref: z.string().optional().describe('Grammar reference like §3890'),
});

const ArmbrusterEntry = z.object({
  // Core
  headword: z.string().describe('The headword EXACTLY as printed, including ˘ connectors, hyphens, diacritics. E.g.: -á˘, á˘, áb, ábti˘r˘'),
  headword_normalized: z.string().describe('Headword without ˘ and with simplified diacritics for search: á→a, é→e, etc.'),
  pos: z.string().optional().describe('Part of speech: n., v.t., v.i., adj., adv., stat., caus., prep., conj., interj., pron., num., postpos.'),
  grammar_refs: z.array(z.string()).optional().describe('Grammar section references: §939, §§3890-3906, §2374b'),

  // Meanings
  english: z.array(z.string()).describe('Clean English definitions only. Each meaning as separate string.'),

  // Morphology
  object_form: z.string().optional().describe('Objective case form: obj. ág˘w˘, -g˘'),
  plural_form: z.string().optional().describe('Plural form: pl. ánč(1)˘'),
  genitive_form: z.string().optional().describe('Genitive form if given'),
  verb_paradigm: VerbParadigm.optional().describe('Verb conjugation forms'),
  possessive_paradigm: PossessiveParadigm.optional().describe('Possessive pronoun forms'),

  // Compounds & Derived forms
  compounds: z.array(CompoundForm).optional().describe('Compound and derived forms listed with — dashes'),

  // Usage
  usage_examples: z.array(UsageExample).optional().describe('Example sentences with Nubian + English'),

  // Etymology
  etymology: z.string().optional().describe('Etymology in [brackets], may include Arabic script'),

  // Sub-entries
  parent_headword: z.string().optional().describe('If this is a sub-entry, the parent headword'),

  // LLM enrichments
  arabic_translation: z.string().optional().describe('Modern Standard Arabic translation'),
  arabic_script: z.string().optional().describe('Arabic script of the translation'),
  ipa: z.string().optional().describe('IPA pronunciation'),
  categories: z.array(z.string()).describe('Semantic categories'),
  difficulty: z.enum(['beginner', 'intermediate', 'advanced']),
  is_loanword: z.boolean(),
  loanword_source: z.string().optional(),
  example_sentence: z.string().optional().describe('Simple generated example: "Nubian. = English."'),
});

const PageExtraction = z.object({
  book_page_number: z.number().describe('Page number printed on the page'),
  letter_section: z.string().describe('Letter section A-Z'),
  entries: z.array(ArmbrusterEntry),
  extraction_notes: z.string().optional(),
});

// ─────────────────────────────────────────────────────────────
// Screenshot extraction
// ─────────────────────────────────────────────────────────────

async function extractScreenshots(pdfPath: string, outputDir: string, startPage: number, endPage: number) {
  // Use Python via child_process since pypdfium2 is Python-only
  const { execSync } = await import('node:child_process');
  fs.mkdirSync(outputDir, { recursive: true });

  const script = `
import pypdfium2 as pdfium
from pathlib import Path
pdf = pdfium.PdfDocument("${pdfPath}")
out = Path("${outputDir}")
for p in range(${startPage - 1}, min(${endPage}, len(pdf))):
    path = out / f"page_{p+1:04d}.png"
    if path.exists(): continue
    bitmap = pdf[p].render(scale=2)
    bitmap.to_pil().save(str(path), "PNG")
    print(f"  Saved {path.name}")
`;

  try {
    const result = execSync(`../pipeline/.venv/bin/python3 -c '${script.replace(/'/g, "'\\''")}'`, {
      cwd: path.resolve('..'),
      encoding: 'utf-8',
      timeout: 120000,
    });
    if (result.trim()) console.log(result.trim());
  } catch (e) {
    console.log('  Screenshots: using existing files');
  }
}

// ─────────────────────────────────────────────────────────────
// Extraction function
// ─────────────────────────────────────────────────────────────

type HybridInput = {
  imagePath: string;
  ocrHtml: string;
};

const extractPage = ai.fn({
  model: 'google/gemini-3-flash-preview',
  reasoning: { effort: 'high' },
  system: `You are an expert Nubian lexicographer extracting entries from C.H. Armbruster's 1965 "Dongolese Nubian: A Lexicon" (Cambridge University Press).

This is a DONGOLAWI dialect dictionary with extremely dense, richly structured entries.

SOURCE PRIORITY:
1. PAGE IMAGE = ground truth (always trust what you see)
2. OCR HTML = structural hint only (has errors, especially missing ˘ symbol and some diacritics)

CRITICAL — THE ˘ CONNECTOR SYMBOL:
- The ˘ (breve) is a MORPHEME JUNCTURE marker — a small curved mark connecting morphemes
- It looks like a tiny U-shape between word parts
- The OCR CANNOT detect it — it shows hyphens or nothing instead
- YOU MUST read it from the IMAGE and preserve it: tékk˘utir˘, kándig˘étta˘
- If you can see it in the image, include it. If unclear, use ˘ if the word seems compound.

PAGE LAYOUT:
- Dense two or three-column layout
- Bold headwords at the left margin of each entry
- Italic text = English glosses/definitions
- § references = grammar section cross-references
- [brackets] = etymology, may include Arabic script
- — dashes introduce compound/derived forms
- Indented text = sub-entries, verb paradigms, usage examples

ENTRY STRUCTURE:
  HEADWORD (§ref) POS English definition.
    obj. object-form.  pl. plural-form.
    pres. present, perf. perfect, imperat. imperative.
    Nubian example sentence English translation.
    — compound-form meaning.

EXTRACTION RULES:
1. "english" = PURE English meanings only — no Nubian words, no § references, no grammar codes
2. Preserve ALL diacritics: á é í ó ú ā ē ī ō ū ñ č š ž AND the ˘ connector
3. Verb paradigms → verb_paradigm field (pres, perf, imperat, participles)
4. Possessive paradigms → possessive_paradigm (ánn-, énn-, ténn-)
5. Compound/derived forms → compounds array with form + meaning
6. Usage examples → usage_examples with nubian + english
7. headword_normalized: strip ˘, convert á→a, é→e, etc. for search`,
  schema: PageExtraction,
  input: (data: HybridInput) => [
    { type: 'text' as const, text: `Extract all dictionary entries from this Armbruster page. The IMAGE is ground truth — the OCR below is a structural hint only (it MISSES the ˘ connector symbol, read that from the image).\n\n--- OCR HTML (hint only, has errors) ---\n${data.ocrHtml.slice(0, 5000)}` },
    { type: 'image' as const, image: fs.readFileSync(data.imagePath) },
  ],
});

// ─────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────

async function processPages(startPage: number, endPage: number) {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  console.log('Checking screenshots...');
  const screenshotCount = fs.readdirSync(SCREENSHOTS_DIR).filter(f => f.endsWith('.png')).length;
  console.log(`  ${screenshotCount} screenshots available`);
  console.log();

  let totalEntries = 0;

  for (let page = startPage; page <= endPage; page++) {
    const imgPath = path.join(SCREENSHOTS_DIR, `page_${String(page).padStart(4, '0')}.png`);
    const outPath = path.join(OUTPUT_DIR, `page_${String(page).padStart(4, '0')}.json`);

    if (!fs.existsSync(imgPath)) {
      console.log(`  p${page}: no screenshot, skipping`);
      continue;
    }

    if (fs.existsSync(outPath)) {
      const cached = JSON.parse(fs.readFileSync(outPath, 'utf-8'));
      const count = cached.entries?.length || 0;
      totalEntries += count;
      console.log(`  p${page}: cached (${count} entries)`);
      continue;
    }

    // Load OCR HTML hint if available
    const htmlPath = path.join(HTML_DIR, `page_${String(page).padStart(4, '0')}.html`);
    const ocrHtml = fs.existsSync(htmlPath)
      ? fs.readFileSync(htmlPath, 'utf-8')
      : '<p>No OCR available</p>';

    try {
      console.log(`  p${page}: extracting (hybrid + thinking)...`);
      const result = await extractPage({ imagePath: imgPath, ocrHtml });

      fs.writeFileSync(outPath, JSON.stringify(result, null, 2));
      totalEntries += result.entries.length;

      const sample = result.entries[0];
      const notes = result.extraction_notes ? ` [${result.extraction_notes.slice(0, 50)}]` : '';
      console.log(`  p${page}: ${result.entries.length} entries (${result.letter_section})${notes}`);
      if (sample) {
        console.log(`    → ${sample.headword} [${sample.headword_normalized}] = ${sample.english.join(', ')}`);
      }
    } catch (err) {
      console.error(`  p${page}: ERROR — ${(err as Error).message}`);
      fs.writeFileSync(outPath, JSON.stringify({ error: (err as Error).message, entries: [] }));
    }

    await new Promise(r => setTimeout(r, 500));
  }

  // Save combined
  const allEntries: any[] = [];
  for (let page = startPage; page <= endPage; page++) {
    const outPath = path.join(OUTPUT_DIR, `page_${String(page).padStart(4, '0')}.json`);
    if (fs.existsSync(outPath)) {
      const data = JSON.parse(fs.readFileSync(outPath, 'utf-8'));
      if (data.entries) {
        for (const e of data.entries) {
          e._page = page;
          allEntries.push(e);
        }
      }
    }
  }

  const combined = {
    metadata: {
      source: 'Armbruster, C.H. (1965). Dongolese Nubian: A Lexicon. Cambridge University Press.',
      method: 'Canonical: Gemini 3 Flash (thinking=high) vision extraction',
      model: 'google/gemini-3-flash-preview',
      dialect: 'Dongolawi',
      pages_processed: endPage - startPage + 1,
      total_entries: allEntries.length,
    },
    entries: allEntries,
  };

  const combinedPath = path.join(OUTPUT_DIR, 'armbruster_canonical.json');
  fs.writeFileSync(combinedPath, JSON.stringify(combined, null, 2));
  console.log(`\nSaved ${allEntries.length} canonical entries to ${combinedPath}`);
}

// Parse args
const args = process.argv.slice(2);
let startPage = 18, endPage = 22;
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--pages' && args[i + 1]) {
    const [s, e] = args[i + 1].split('-').map(Number);
    startPage = s; endPage = e || s;
  }
}

console.log(`Armbruster Lexicon CANONICAL Extraction`);
console.log(`  Model: google/gemini-3-flash-preview (thinking=high)`);
console.log(`  Pages: ${startPage}-${endPage}`);
console.log(`  Output: ${OUTPUT_DIR}`);
console.log();

processPages(startPage, endPage);
