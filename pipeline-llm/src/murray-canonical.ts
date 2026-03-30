/**
 * CANONICAL extraction: Maximum-value extraction per page.
 *
 * Beyond what's printed, the LLM adds:
 *   - Arabic translation (modern standard)
 *   - IPA pronunciation
 *   - Simplified romanization (no diacritics, keyboard-friendly)
 *   - Semantic categories for language learning
 *   - Difficulty level (beginner/intermediate/advanced)
 *   - Root morpheme grouping
 *   - Loanword detection
 *   - Example sentence generation
 *   - Archaic/modern usage flag
 *
 * Run:
 *   npx tsx src/murray-canonical.ts --pages 49-52
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

const SCREENSHOTS_DIR = path.resolve('../output/murray/screenshots');
const HTML_DIR = path.resolve('../output/murray/html');
const OUTPUT_DIR = path.resolve('../output/murray/llm-canonical');

// ─────────────────────────────────────────────────────────────
// Canonical Schema — maximum value per entry
// ─────────────────────────────────────────────────────────────

const DialectForm = z.object({
  dialect: z.string().describe('Dialect code: K, M, D, KD, KM, DM, KDM, Dai, Mid, Kdr, ON (Old Nubian)'),
  form: z.string().describe('The word in this dialect\'s romanization'),
  plural: z.string().optional().describe('Plural form if known'),
});

const Cognate = z.object({
  language: z.string().describe('Arabic, Hamitic, Semitic, Nilotic, Egyptian, Coptic, Central African'),
  form: z.string(),
  meaning: z.string().optional(),
});

const CanonicalEntry = z.object({
  // ── From the page ──
  headword: z.string().describe('Primary Nubian headword'),
  pos: z.string().optional().describe('Part of speech: n, v.t, v.i, adj, adv, conj, pron, interj, postpos'),
  forms: z.array(DialectForm).describe('All dialect forms'),
  english: z.array(z.string()).describe('Clean English meanings only — no Nubian, no references'),
  variant_forms: z.array(z.string()).optional().describe('Compound/derived forms with — dashes'),
  usage_examples: z.array(z.string()).optional().describe('Example sentences from the book'),
  cognates: z.array(Cognate).optional().describe('Comparative cognates from right column'),

  // ── LLM-generated enrichments ──
  arabic_translation: z.string().optional().describe('Modern Standard Arabic translation (الترجمة العربية)'),
  arabic_script: z.string().optional().describe('The Arabic script form of the translation'),
  ipa: z.string().optional().describe('IPA pronunciation of the headword, e.g., /ˈaː.man/'),
  simple_roman: z.string().describe('Keyboard-friendly romanization with no diacritics: ā→aa, é→e, ō→oo, ū→uu, etc.'),
  categories: z.array(z.string()).describe('Semantic categories: animal, food, body, family, nature, tool, house, clothing, agriculture, religion, emotion, action, number, color, time, greeting, grammar'),
  difficulty: z.enum(['beginner', 'intermediate', 'advanced']).describe('Learning difficulty: beginner=common everyday words, intermediate=less common, advanced=rare/specialized/archaic'),
  root: z.string().optional().describe('Root morpheme if this word is derived (e.g., ā-dūl has root ā)'),
  is_loanword: z.boolean().describe('Whether this is borrowed from Arabic, Turkish, or another language'),
  loanword_source: z.string().optional().describe('Source language of loanword if is_loanword=true'),
  is_archaic: z.boolean().describe('Whether this word is likely archaic/no longer in common use'),
  example_sentence: z.string().optional().describe('A simple example sentence using this word in Nubian (romanized) with English translation. Format: "Nubian sentence. = English translation."'),
});

const PageExtraction = z.object({
  book_page_number: z.number(),
  letter_section: z.string(),
  entries: z.array(CanonicalEntry),
  extraction_notes: z.string().optional(),
});

// ─────────────────────────────────────────────────────────────
// Canonical extraction function
// ─────────────────────────────────────────────────────────────

type CanonicalInput = {
  imagePath: string;
  ocrHtml: string;
};

const extractCanonical = ai.fn({
  model: 'google/gemini-3-flash-preview',
  reasoning: { effort: 'high' },
  system: `You are an expert Nubian lexicographer and linguist. You are extracting entries from G.W. Murray's 1923 "An English-Nubian Comparative Dictionary" and ENRICHING each entry with additional linguistic data.

SOURCE PRIORITY:
1. PAGE IMAGE = ground truth (always trust the image)
2. OCR HTML = structural hints only (may have errors)

PAGE LAYOUT:
- LEFT: headword (bold) + dialect codes (K.=Kenzi, M.=Mahas, D.=Dongolawi, KD, KM, DM, KDM, Dai.=Dairawi, Mid.=Midob, Kdr.=Kordofan) + POS + definition + variants + examples
- RIGHT: comparative cognates (AR.=Arabic, HAM.=Hamitic, SEM.=Semitic, NIL.=Nilotic, EG.=Egyptian)

ENRICHMENT RULES:
- arabic_translation: Provide the Modern Standard Arabic equivalent (not the comparative cognate from the book). For "water" → "ماء", for "heart" → "قلب"
- arabic_script: The Arabic script of the translation
- ipa: Best-effort IPA based on Murray's romanization system. Use /ˈ/ for stress on accented vowels.
- simple_roman: Strip ALL diacritics: ā→aa, á→a, ē→ee, é→e, ī→ii, ō→oo, ú→u, ū→uu, ñ→ny, š→sh, č→ch, ǧ→j
- categories: Pick 1-3 from: animal, food, body, family, nature, water, tool, house, clothing, agriculture, religion, emotion, action, number, color, time, greeting, grammar, place, weather, military, plant, music
- difficulty: beginner=top 500 everyday words (water, food, house, come, go), intermediate=common but less basic, advanced=specialized/rare/archaic
- root: If derived (e.g., ā-dūl from ā "heart"), give the root. If standalone, leave empty.
- is_loanword: true if clearly from Arabic (AR. cognate matches closely) or another language
- is_archaic: true if the word seems specialized, regional (Kordofan/Dairawi only), or from Old Nubian texts
- example_sentence: Generate a simple natural sentence. E.g., for aman (water): "aman ēn kōn-ir. = The water is in the river."

CRITICAL: The "english" array must be PURE English meanings. No Nubian words, no dialect codes, no comparative data.`,
  schema: PageExtraction,
  input: (data: CanonicalInput) => [
    { type: 'text' as const, text: `Extract and enrich all dictionary entries from this page.\n\n--- OCR HTML (structural hint only) ---\n${data.ocrHtml.slice(0, 4000)}` },
    { type: 'image' as const, image: fs.readFileSync(data.imagePath) },
  ],
});

// ─────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────

async function processPages(startPage: number, endPage: number) {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  let totalEntries = 0;
  const allPages: any[] = [];

  for (let page = startPage; page <= endPage; page++) {
    const imgPath = path.join(SCREENSHOTS_DIR, `page_${String(page).padStart(4, '0')}.png`);
    const htmlPath = path.join(HTML_DIR, `page_${String(page).padStart(4, '0')}.html`);
    const outPath = path.join(OUTPUT_DIR, `page_${String(page).padStart(4, '0')}.json`);

    if (!fs.existsSync(imgPath)) {
      console.log(`  p${page}: no screenshot, skipping`);
      continue;
    }

    if (fs.existsSync(outPath)) {
      const cached = JSON.parse(fs.readFileSync(outPath, 'utf-8'));
      const count = cached.entries?.length || 0;
      totalEntries += count;
      allPages.push({ pdf_page: page, extraction: cached });
      console.log(`  p${page}: cached (${count} entries)`);
      continue;
    }

    const ocrHtml = fs.existsSync(htmlPath)
      ? fs.readFileSync(htmlPath, 'utf-8')
      : '<p>No OCR available</p>';

    try {
      console.log(`  p${page}: extracting (canonical + thinking)...`);
      const result = await extractCanonical({ imagePath: imgPath, ocrHtml });

      fs.writeFileSync(outPath, JSON.stringify(result, null, 2));
      totalEntries += result.entries.length;
      allPages.push({ pdf_page: page, extraction: result });

      // Show a sample entry
      const sample = result.entries[0];
      console.log(`  p${page}: ${result.entries.length} entries (${result.letter_section})`);
      if (sample) {
        console.log(`    sample: ${sample.headword} [${sample.simple_roman}] = ${sample.english.join(', ')} | ar: ${sample.arabic_translation || '—'} | ${sample.categories.join(', ')} | ${sample.difficulty}`);
      }
    } catch (err) {
      console.error(`  p${page}: ERROR — ${(err as Error).message}`);
      fs.writeFileSync(outPath, JSON.stringify({ error: (err as Error).message, entries: [] }));
    }

    await new Promise(r => setTimeout(r, 500));
  }

  // Save combined
  const combined = {
    metadata: {
      source: 'Murray 1923',
      method: 'Canonical: Gemini 3 Flash (thinking=high) + OCR hints + LLM enrichment',
      model: 'google/gemini-3-flash-preview',
      enrichments: ['arabic_translation', 'ipa', 'simple_roman', 'categories', 'difficulty', 'root', 'is_loanword', 'is_archaic', 'example_sentence'],
      pages_processed: allPages.length,
      total_entries: totalEntries,
    },
    pages: allPages,
  };

  fs.writeFileSync(
    path.join(OUTPUT_DIR, 'murray_canonical.json'),
    JSON.stringify(combined, null, 2)
  );
  console.log(`\nSaved ${totalEntries} canonical entries to ${OUTPUT_DIR}/murray_canonical.json`);
}

const args = process.argv.slice(2);
let startPage = 49, endPage = 52;
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--pages' && args[i + 1]) {
    const [s, e] = args[i + 1].split('-').map(Number);
    startPage = s; endPage = e || s;
  }
}

console.log(`Murray CANONICAL Extraction Pipeline`);
console.log(`  Model: google/gemini-3-flash-preview (thinking=high)`);
console.log(`  Output: ${OUTPUT_DIR}`);
console.log();

processPages(startPage, endPage);
