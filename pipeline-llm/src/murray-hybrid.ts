/**
 * HYBRID extraction: Image (ground truth) + OCR HTML (structural hints) + Thinking.
 *
 * Sends each page as:
 *   1. The screenshot image (visual ground truth)
 *   2. The OCR HTML (structural hints — bold/italic/list detection)
 *   3. A prompt that explicitly tells the LLM to use the image as truth
 *      and the HTML only as a structural guide
 *
 * Uses Gemini 3 Flash with reasoning=high for deeper analysis.
 *
 * Run:
 *   npx tsx src/murray-hybrid.ts --pages 49-52
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
const HTML_DIR = path.resolve('../output/murray/html');
const OUTPUT_DIR = path.resolve('../output/murray/llm-hybrid');

// ─────────────────────────────────────────────────────────────
// Schema
// ─────────────────────────────────────────────────────────────

const DialectForm = z.object({
  dialect: z.string().describe('Dialect code: K, M, D, KD, KM, DM, KDM, Dai, Mid, Kdr'),
  romanization: z.string().describe('The word form in this dialect'),
});

const Cognate = z.object({
  language: z.string().describe('Language: Arabic, Hamitic, Semitic, Nilotic, Egyptian, Central African, Coptic'),
  form: z.string().describe('The cognate word'),
  meaning: z.string().optional().describe('Meaning of the cognate if given'),
});

const DictionaryEntry = z.object({
  headword: z.string().describe('The Nubian headword being defined'),
  pos: z.string().optional().describe('Part of speech: s, v.t, v.i, adj, adv, conj, pron, interj, postpos'),
  forms: z.array(DialectForm).describe('All dialect forms listed for this headword'),
  english: z.array(z.string()).describe('Clean English meanings ONLY. No Nubian words, no dialect codes, no comparative references. Each distinct meaning as a separate string.'),
  variant_forms: z.array(z.string()).optional().describe('Compound or derived forms listed with — (e.g., ā-dūl pride, ā-dūl-kōl proud)'),
  usage_examples: z.array(z.string()).optional().describe('Example sentences or phrases showing usage'),
  cognates: z.array(Cognate).optional().describe('Comparative cognates from the RIGHT column'),
  cross_references: z.array(z.string()).optional().describe('Cross-references like "s. word" or "cf. word"'),
});

const PageExtraction = z.object({
  book_page_number: z.number().describe('The page number printed on the page (1-189)'),
  letter_section: z.string().describe('Current letter section (A-Z)'),
  page_header: z.string().optional().describe('Running header at top of page (e.g., "abā—abi-n")'),
  entries: z.array(DictionaryEntry),
  extraction_notes: z.string().optional().describe('Any issues or ambiguities you noticed while extracting'),
});

// ─────────────────────────────────────────────────────────────
// Hybrid extraction function
// ─────────────────────────────────────────────────────────────

type HybridInput = {
  imagePath: string;
  ocrHtml: string;
};

const extractPageHybrid = ai.fn({
  model: 'google/gemini-3-flash-preview',
  reasoning: { effort: 'high' },
  system: `You are an expert Nubian lexicographer extracting entries from G.W. Murray's 1923 "An English-Nubian Comparative Dictionary" (Oxford University Press).

IMPORTANT INSTRUCTIONS:
1. The PAGE IMAGE is your PRIMARY source of truth — read directly from the scanned page
2. The OCR HTML is provided ONLY as a structural hint — it may have errors, missing entries, or garbled text
3. When the image and HTML disagree, ALWAYS trust the image

PAGE LAYOUT:
- LEFT side: headword (bold), dialect codes in italic (K.=Kenzi, M.=Mahas, D.=Dongolawi, KD, KM, DM, KDM, Dai.=Dairawi, Mid.=Midob, Kdr.=Kordofan), part of speech, English definition, variant forms with — dashes, usage examples
- RIGHT side: comparative cognates prefixed with AR.=Arabic, HAM.=Hamitic, SEM.=Semitic, NIL.=Nilotic, EG.=Egyptian, CENT.=Central African, Cf.=compare

EXTRACTION RULES:
- The "english" field must contain ONLY clean English meanings — absolutely no Nubian words, no dialect codes, no comparative language references
- Split multiple meanings: "heart, soul, mind, self" → ["heart", "soul", "mind", "self"]
- Capture variant/compound forms separately in "variant_forms" (e.g., "ā-dūl" = pride)
- Each entry's "forms" should list EVERY dialect the word appears in with its form in that dialect
- Cognates go in the cognates array, NOT in the english definitions
- Skip page running headers (like "abā—abi-n" at the top)
- Include ALL entries — don't skip any, even short ones`,
  schema: PageExtraction,
  input: (data: HybridInput) => [
    { type: 'text' as const, text: `Extract all dictionary entries from this page. Use the image as ground truth. The OCR HTML below is provided as a structural hint only — it may contain errors.\n\n--- OCR HTML (structural hint, may have errors) ---\n${data.ocrHtml.slice(0, 4000)}` },
    { type: 'image' as const, image: fs.readFileSync(data.imagePath) },
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
    const htmlPath = path.join(HTML_DIR, `page_${String(page).padStart(4, '0')}.html`);

    if (!fs.existsSync(imgPath)) {
      console.log(`  p${page}: no screenshot, skipping`);
      continue;
    }

    const outPath = path.join(OUTPUT_DIR, `page_${String(page).padStart(4, '0')}.json`);
    if (fs.existsSync(outPath)) {
      const cached = JSON.parse(fs.readFileSync(outPath, 'utf-8'));
      const count = cached.entries?.length || 0;
      totalEntries += count;
      allResults.push({ pdf_page: page, extraction: cached });
      console.log(`  p${page}: cached (${count} entries)`);
      continue;
    }

    // Load OCR HTML if available
    const ocrHtml = fs.existsSync(htmlPath)
      ? fs.readFileSync(htmlPath, 'utf-8')
      : '<p>No OCR available for this page</p>';

    try {
      console.log(`  p${page}: extracting (hybrid + thinking)...`);
      const result = await extractPageHybrid({ imagePath: imgPath, ocrHtml });

      fs.writeFileSync(outPath, JSON.stringify(result, null, 2));
      totalEntries += result.entries.length;
      allResults.push({ pdf_page: page, extraction: result });

      const notes = result.extraction_notes ? ` [${result.extraction_notes.slice(0, 60)}]` : '';
      console.log(`  p${page}: ${result.entries.length} entries (${result.letter_section})${notes}`);
    } catch (err) {
      console.error(`  p${page}: ERROR — ${(err as Error).message}`);
      fs.writeFileSync(outPath, JSON.stringify({ error: (err as Error).message, entries: [] }));
    }

    await new Promise(r => setTimeout(r, 500));
  }

  // Save combined
  const combined = {
    metadata: {
      source: 'Murray 1923 — An English-Nubian Comparative Dictionary',
      method: 'Hybrid: Gemini 3 Flash (thinking=high) + OCR HTML hints',
      model: 'google/gemini-3-flash-preview',
      reasoning: 'high',
      pages_processed: allResults.length,
      total_entries: totalEntries,
    },
    pages: allResults,
  };

  const combinedPath = path.join(OUTPUT_DIR, 'murray_hybrid_extracted.json');
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

console.log(`Murray HYBRID Extraction Pipeline`);
console.log(`  Model: google/gemini-3-flash-preview (thinking=high)`);
console.log(`  Image: ${SCREENSHOTS_DIR}`);
console.log(`  HTML hints: ${HTML_DIR}`);
console.log(`  Output: ${OUTPUT_DIR}`);
console.log();

processPages(startPage, endPage);
