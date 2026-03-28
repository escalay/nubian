/**
 * LLM-based extraction pipeline for Murray 1923 dictionary pages.
 *
 * Sends each page screenshot to a vision LLM (Gemini) with a structured
 * Zod schema matching the NubianEntry format. Produces comparable JSON
 * output to the OCR-based pipeline for cross-validation.
 *
 * Run:
 *   npx tsx src/murray-extract.ts --pages 49-52
 *   npx tsx src/murray-extract.ts --pages 49-237  # full run
 */

import { createAiFn } from 'funcai';
import { openrouter } from 'funcai/providers/openrouter';
import { z } from 'zod';
import fs from 'fs';
import path from 'path';

const OPENROUTER_API_KEY = process.env.OPENROUTER_API_KEY
  || 'REDACTED';

const ai = createAiFn({
  provider: openrouter({ apiKey: OPENROUTER_API_KEY }),
});

const SCREENSHOTS_DIR = path.resolve('../output/murray/screenshots');
const OUTPUT_DIR = path.resolve('../output/murray/llm');

// ─────────────────────────────────────────────────────────────
// Schema — matches the unified NubianEntry structure
// ─────────────────────────────────────────────────────────────

const DialectForm = z.object({
  dialect: z.string().describe('Dialect code: K (Kenzi), M (Mahas), D (Dongolawi), KD, KM, DM, KDM, Dai (Dairawi), Mid (Midob), Kdr (Kordofan)'),
  romanization: z.string().describe('The word in this dialect'),
});

const Cognate = z.object({
  language: z.string().describe('Language family: Arabic, Hamitic, Semitic, Nilotic, Egyptian, Central African'),
  form: z.string().describe('The cognate word form'),
});

const DictionaryEntry = z.object({
  headword: z.string().describe('The main dictionary headword (romanized Nubian word)'),
  pos: z.string().optional().describe('Part of speech: s (noun), v.t (transitive verb), v.i (intransitive verb), adj, adv, conj, pron, interj, postpos'),
  forms: z.array(DialectForm).describe('The word in each dialect it appears in'),
  english: z.array(z.string()).describe('English meaning(s) — each meaning as a separate string. Just the English translations, no Nubian forms or comparative references'),
  cognates: z.array(Cognate).optional().describe('Comparative cognates from the right column (AR., HAM., SEM., NIL., etc.)'),
});

const PageExtraction = z.object({
  page_number: z.number().describe('The book page number shown on the page'),
  letter_section: z.string().describe('The letter section (A-Z) this page belongs to'),
  entries: z.array(DictionaryEntry).describe('All dictionary entries on this page'),
});

// ─────────────────────────────────────────────────────────────
// LLM extraction function
// ─────────────────────────────────────────────────────────────

const extractPage = ai.fn({
  model: 'google/gemini-3-flash-preview',
  system: `You are an expert Nubian lexicographer extracting dictionary entries from G.W. Murray's 1923 "An English-Nubian Comparative Dictionary".

Each page has a two-column layout:
- LEFT COLUMN: headword (bold), dialect codes (K.=Kenzi, M.=Mahas, D.=Dongolawi, KD, KM, DM, KDM, Dai.=Dairawi, Mid.=Midob, Kdr.=Kordofan), part of speech, English definition, usage examples, and variant/compound forms
- RIGHT COLUMN: comparative cognates from other languages (AR.=Arabic, HAM.=Hamitic, SEM.=Semitic, NIL.=Nilotic, EG.=Egyptian, CENT.=Central African)

Rules:
- Extract ONLY the clean English meaning(s) — no Nubian words, no dialect codes, no comparative references
- Split multiple meanings into separate array items: "heart, soul, mind" → ["heart", "soul", "mind"]
- The headword is the Nubian word being defined (leftmost bold text of each entry)
- Each entry may appear in multiple dialects — list all dialect forms
- Cognates are in the right column, prefixed by AR., HAM., SEM., NIL., etc.
- Skip page headers (e.g., "abā—abi-n") — these are running headers, not entries
- If a headword starts with "-" it's a suffix/prefix — include the dash`,
  schema: PageExtraction,
  input: (imagePath: string) => [
    { type: 'text' as const, text: 'Extract all dictionary entries from this page of Murray\'s Nubian dictionary. Be thorough — capture every headword.' },
    { type: 'image' as const, image: fs.readFileSync(imagePath) },
  ],
});

// ─────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────

async function processPages(startPage: number, endPage: number) {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  const allResults: Array<{ pdf_page: number; extraction: z.infer<typeof PageExtraction> }> = [];
  let totalEntries = 0;

  for (let page = startPage; page <= endPage; page++) {
    const imgPath = path.join(SCREENSHOTS_DIR, `page_${String(page).padStart(4, '0')}.png`);

    if (!fs.existsSync(imgPath)) {
      console.log(`  p${page}: no screenshot, skipping`);
      continue;
    }

    // Check if already processed
    const outPath = path.join(OUTPUT_DIR, `page_${String(page).padStart(4, '0')}.json`);
    if (fs.existsSync(outPath)) {
      const cached = JSON.parse(fs.readFileSync(outPath, 'utf-8'));
      totalEntries += cached.entries?.length || 0;
      allResults.push({ pdf_page: page, extraction: cached });
      console.log(`  p${page}: cached (${cached.entries?.length || 0} entries)`);
      continue;
    }

    try {
      console.log(`  p${page}: extracting...`);
      const result = await extractPage(imgPath);

      // Save individual page result
      fs.writeFileSync(outPath, JSON.stringify(result, null, 2));
      totalEntries += result.entries.length;
      allResults.push({ pdf_page: page, extraction: result });
      console.log(`  p${page}: ${result.entries.length} entries (section ${result.letter_section})`);
    } catch (err) {
      console.error(`  p${page}: ERROR — ${(err as Error).message}`);
      // Save error marker
      fs.writeFileSync(outPath, JSON.stringify({ error: (err as Error).message, entries: [] }));
    }

    // Rate limiting — be gentle
    await new Promise(r => setTimeout(r, 1000));
  }

  // Save combined output
  const combined = {
    metadata: {
      source: 'Murray 1923 — An English-Nubian Comparative Dictionary',
      method: 'LLM vision extraction (Gemini 2.5 Flash via OpenRouter)',
      pages_processed: allResults.length,
      total_entries: totalEntries,
    },
    pages: allResults,
  };

  const combinedPath = path.join(OUTPUT_DIR, 'murray_llm_extracted.json');
  fs.writeFileSync(combinedPath, JSON.stringify(combined, null, 2));
  console.log(`\nSaved ${totalEntries} entries across ${allResults.length} pages to ${combinedPath}`);
}

// Parse args
const args = process.argv.slice(2);
let startPage = 49;
let endPage = 52;

for (let i = 0; i < args.length; i++) {
  if (args[i] === '--pages' && args[i + 1]) {
    const [s, e] = args[i + 1].split('-').map(Number);
    startPage = s;
    endPage = e || s;
  }
}

console.log(`Murray LLM Extraction Pipeline`);
console.log(`  Pages: ${startPage}-${endPage}`);
console.log(`  Screenshots: ${SCREENSHOTS_DIR}`);
console.log(`  Output: ${OUTPUT_DIR}`);
console.log();

processPages(startPage, endPage);
