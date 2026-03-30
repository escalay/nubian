/**
 * Column-based canonical extraction for Armbruster's Lexicon.
 *
 * Processes each of the 3 columns per page as a separate LLM call.
 * Each column gets a 4x resolution crop for maximum readability.
 * Results are merged per-page with column provenance.
 *
 * Run:
 *   npx tsx src/armbruster-columns.ts --pages 19-20   # test (6 columns)
 *   npx tsx src/armbruster-columns.ts --pages 18-222  # full run
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

const COLUMNS_DIR = path.resolve('../output/armbruster/columns');
const HTML_DIR = path.resolve('../output/armbruster/html');
const OUTPUT_DIR = path.resolve('../output/armbruster/llm-columns');

// ─────────────────────────────────────────────────────────────
// Schema
// ─────────────────────────────────────────────────────────────

const VerbForms = z.object({
  present: z.string().optional(),
  perfect: z.string().optional(),
  imperative: z.string().optional(),
  participle: z.string().optional(),
});

const UsageExample = z.object({
  nubian: z.string().describe('Nubian text with ˘ connectors preserved'),
  english: z.string(),
});

const CompoundForm = z.object({
  form: z.string().describe('Compound form with ˘ preserved'),
  meaning: z.string(),
  grammar_ref: z.string().optional(),
});

const Entry = z.object({
  headword: z.string().describe('Exact headword with ˘ connectors and diacritics'),
  headword_simple: z.string().describe('Simplified for search: no ˘, á→a, é→e, ā→aa'),
  pos: z.string().optional().describe('n., v.t., v.i., adj., adv., stat.'),
  grammar_refs: z.array(z.string()).optional().describe('§ references'),
  english: z.array(z.string()).describe('Clean English meanings ONLY'),
  object_form: z.string().optional().describe('obj. form'),
  plural_form: z.string().optional().describe('pl. form'),
  verb_forms: VerbForms.optional(),
  compounds: z.array(CompoundForm).optional(),
  usage_examples: z.array(UsageExample).optional(),
  etymology: z.string().optional().describe('Text in [brackets]'),
  arabic_translation: z.string().optional(),
  arabic_script: z.string().optional(),
  ipa: z.string().optional(),
  categories: z.array(z.string()),
  difficulty: z.enum(['beginner', 'intermediate', 'advanced']),
  is_loanword: z.boolean(),
});

const ColumnExtraction = z.object({
  entries: z.array(Entry),
  column_notes: z.string().optional(),
});

// ─────────────────────────────────────────────────────────────
// Extraction — one column at a time
// ─────────────────────────────────────────────────────────────

const extractColumn = ai.fn({
  model: 'google/gemini-3-flash-preview',
  reasoning: { effort: 'high' },
  system: `You are extracting entries from ONE COLUMN of Armbruster's "Dongolese Nubian: A Lexicon" (1965).

This is a DONGOLAWI dialect dictionary. You are seeing a SINGLE COLUMN cropped from a 3-column page at high resolution.

THE ˘ SYMBOL (CRITICAL):
- The ˘ (breve) connects morphemes — it's a small curved mark ABOVE the line
- It is NOT a hyphen. Examples: tékk˘utir˘, áb˘an, kándig˘étta˘
- LOOK CAREFULLY for it in the image — it appears between word parts
- Preserve it exactly in headwords, compounds, and examples

ENTRY FORMAT:
- BOLD HEADWORD (§ref) POS English definition in italic
  obj. objective. pl. plural.
  pres. present, perf. perfect, imperat. imperative.
  Nubian example = English translation.
  — compound-form English meaning.
  [etymology with Arabic/Greek script]

RULES:
1. "english" = ONLY clean English meanings. No Nubian, no §refs
2. Preserve ˘ in ALL Nubian text (headwords, compounds, examples)
3. Verb forms (pres., perf., imperat.) → verb_forms
4. Compounds with — dashes → compounds array
5. Example sentences → usage_examples
6. [bracketed text] → etymology
7. headword_simple: strip ˘ and diacritics for search
8. Add Arabic translation, IPA, categories, difficulty
9. Entries that continue from a previous column may start mid-entry — extract what you can`,
  schema: ColumnExtraction,
  input: (imagePath: string) => [
    { type: 'text' as const, text: 'Extract all dictionary entries from this column. Read carefully — preserve the ˘ connector symbol.' },
    { type: 'image' as const, image: fs.readFileSync(imagePath) },
  ],
});

// ─────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────

async function processPages(startPage: number, endPage: number) {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  let totalEntries = 0;
  let totalColumns = 0;

  for (let page = startPage; page <= endPage; page++) {
    const pageOutPath = path.join(OUTPUT_DIR, `page_${String(page).padStart(4, '0')}.json`);

    if (fs.existsSync(pageOutPath)) {
      const cached = JSON.parse(fs.readFileSync(pageOutPath, 'utf-8'));
      const count = cached.entries?.length || 0;
      totalEntries += count;
      console.log(`  p${page}: cached (${count} entries)`);
      continue;
    }

    const pageEntries: any[] = [];
    let hasColumns = false;

    for (let col = 1; col <= 3; col++) {
      const colPath = path.join(COLUMNS_DIR, `page_${String(page).padStart(4, '0')}_col${col}.png`);

      if (!fs.existsSync(colPath)) continue;
      hasColumns = true;

      try {
        const result = await extractColumn(colPath);
        totalColumns++;

        for (const entry of result.entries) {
          pageEntries.push({
            ...entry,
            _column: col,
            _page: page,
          });
        }

        if (result.entries.length > 0) {
          const sample = result.entries[0];
          console.log(`  p${page} col${col}: ${result.entries.length} entries → ${sample.headword} [${sample.headword_simple}] = ${sample.english[0] || '?'}`);
        } else {
          console.log(`  p${page} col${col}: 0 entries`);
        }
      } catch (err) {
        console.error(`  p${page} col${col}: ERROR — ${(err as Error).message}`);
      }

      await new Promise(r => setTimeout(r, 300));
    }

    if (!hasColumns) {
      console.log(`  p${page}: no columns found, skipping`);
      continue;
    }

    // Save page result
    const pageResult = {
      page_number: page,
      total_entries: pageEntries.length,
      entries: pageEntries,
    };

    fs.writeFileSync(pageOutPath, JSON.stringify(pageResult, null, 2));
    totalEntries += pageEntries.length;
    console.log(`  p${page}: TOTAL ${pageEntries.length} entries`);
  }

  // Save combined
  const allEntries: any[] = [];
  for (let page = startPage; page <= endPage; page++) {
    const pageOutPath = path.join(OUTPUT_DIR, `page_${String(page).padStart(4, '0')}.json`);
    if (fs.existsSync(pageOutPath)) {
      const data = JSON.parse(fs.readFileSync(pageOutPath, 'utf-8'));
      if (data.entries) allEntries.push(...data.entries);
    }
  }

  const combined = {
    metadata: {
      source: 'Armbruster, C.H. (1965). Dongolese Nubian: A Lexicon.',
      method: 'Column-split canonical: 4x resolution, 3 columns per page, Gemini 3 Flash (thinking=high)',
      model: 'google/gemini-3-flash-preview',
      dialect: 'Dongolawi',
      columns_processed: totalColumns,
      total_entries: allEntries.length,
    },
    entries: allEntries,
  };

  fs.writeFileSync(path.join(OUTPUT_DIR, 'armbruster_columns.json'), JSON.stringify(combined, null, 2));
  console.log(`\nSaved ${allEntries.length} entries (${totalColumns} columns) to armbruster_columns.json`);
}

const args = process.argv.slice(2);
let startPage = 19, endPage = 20;
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--pages' && args[i + 1]) {
    const [s, e] = args[i + 1].split('-').map(Number);
    startPage = s; endPage = e || s;
  }
}

console.log(`Armbruster COLUMN Extraction Pipeline`);
console.log(`  Model: google/gemini-3-flash-preview (thinking=high)`);
console.log(`  Columns: ${COLUMNS_DIR}`);
console.log(`  Pages: ${startPage}-${endPage} (${(endPage - startPage + 1) * 3} columns)`);
console.log();

processPages(startPage, endPage);
