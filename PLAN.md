# Urdu Education-Domain Corpus: Scraping, LLM Annotation & Approval Plan

**Status:** v1.3 — decisions locked (§9); tag schema finalized to the 10-label set (§5).
**Goal:** Build an education-domain Urdu NER corpus (textbook + education-web text), annotated via free-tier LLMs with human approval, to complement ZUNERA (primary general-domain training set, 1.19M tokens). Final use: domain evaluation set + optional fine-tuning slice. This is the repurposed "Option A" pipeline from `Urdu_NER_Dataset_Report.pdf`, narrowed per the ZUNERA findings.

---

## 1. Scope & Targets

| Item | Decision |
|---|---|
| Domain | Education (textbooks, education news). General domain is already covered by ZUNERA. |
| Sources | Textbook board PDFs (copyable-text only) + education web pages |
| Pilot size | **500 sentences** — 100% human-reviewed before scaling |
| Corpus size | **Maximize** within free-API throughput: pool 15–20K sentences, approve as much as possible; hard floor **5,000 approved sentences** |
| Tag schema | **10 tags + `O`** (see §5): PER, LOC, PLACE, JOBTITLE, ORG, TIME, DAY, EVENT, PHONE, CNIC — ZUNERA's 23 tags collapse into this set via a fixed mapping; PHONE/CNIC are synthetic by design |
| Format | CoNLL/BIO2 (token TAB tag, blank line = sentence boundary), one file per split |
| Golden rule | LLM output is never the gold standard. Every sentence in the final corpus is human-approved. |

---

## 2. Data Sources

### 2a. Textbook boards (all free; verified Nov 2026)

| Board | Portal | Status | How to fetch |
|---|---|---|---|
| Sindh Textbook Board (STBB), Jamshoro | `portal.stbb.edu.pk/ebooks` | ✅ live, but **books are image-only** | Class listing: `class.php?id=…&medium=Urdu` (ids: 17=Kachi, 1=I, 3=II, 4=III, 5=IV, 6=V, 7=VI, 8=VII, 9=VIII, 10=IX, 11=X, 14=XI, 15=XII). Book pages: `book.php?id=…`. PDFs: `pdf_proxy.php?id=…&download=1`. ~10 MB/book at ~40 KB/s → polite concurrency 1–2, resumable range requests. **Survey (Nov 2026): 26 books probed across all classes & years 2020–2026 — every one has zero fonts / zero text layer (exported via PScript→Distiller as page images).** |
| Punjab (PCTB/PECTAA) | `pctb.punjab.gov.pk/E-Books` | ⚠️ official site under maintenance; mirrors work | `taleem360.com` (286 Punjab books; download endpoint `/download/<slug>?f=<token>` → 302 to `cdn1.taleem360.com`, Cloudflare-fast) |
| Federal (FBISE) via NBF | `nbf.org.pk` | ⚠️ blocks automated access | taleem360 Federal categories work |
| AIOU (open university) | taleem360 AIOU categories | works | University-level Urdu-medium soft books |

**🔍 Text-layer survey — CONCLUSIVE (Nov 2026): every Pakistani textbook pipeline is image-only.**
Probed with real downloads: STBB 26 books (all classes, 2020–2026, PScript→Distiller), PECTAA Punjab newest Urdu (CorelDRAW 2025), FBISE/NBF Federal Urdu (Ghostscript/PDF24), AIOU Education (Acrobat Image Conversion). **4/4 pipelines: zero embedded Urdu fonts, zero extractable text** — the only text objects are site watermarks. Boards rasterize pages (anti-piracy). Under decision #5 (copyable-only) textbook yield = **0**; textbooks are reachable only via OCR (§9).

**Which books:** Urdu (اردو) readers for every class + Urdu-medium Islamiat (Mazhabi Taleemat), Social Studies (Mashrati Uloom), Science, Civics/Education. These carry stories, essays, biographies, dialogues — the richest NER content. Skip Mathematics problem sets (number lists, no prose).

**Grade coverage (decision #4):** all classes, Kachi → XII. Sampling weighted toward Class 4–12 (longer prose, more named entities); lower classes kept at lower weight — heavy in استاد/اسکول/سبق vocabulary, exactly the teacher signal we want.

**Copyable-text only (decision #5):** only PDFs with a real text layer would be used. The Nov 2026 survey proved **all Pakistani board pipelines are image-only** (see above), so textbook data now depends on the §9 open decision (web-first vs. free-tier OCR).

### 2b. Education web sources (scraped)

| Source | Content |
|---|---|
| BBC Urdu — Taleem section | Education news, formal prose |
| Jang / Roznama education desk, Daily Express education pages | National education coverage |
| Independent Urdu — education | Education reporting |
| HEC website (announcements, scholarships) | Institutional ORG/DATE-dense prose |
| University websites (HEC-recognized, Urdu pages) | Local orgs, teacher names, designations |

All scraping respects `robots.txt` and records source URL + fetch date per document.

---

## 3. Pipeline Architecture

```
[S1] Fetch ──► [S2] Extract (text layer only) ──► [S3] Validate ──► [S4] Clean ──► [S5] Sentence pool
   │              │ web: Scrapy                   │ heuristics       │ Unicode NFC      │ filter + dedupe
   │              │   + trafilatura               │ (free, no OCR)   │ boilerplate out  │ + stratified sampling
   └─ PDFs ──────►│ PDFs: PyMuPDF                │ reject bad books │ fix artifacts    │
                  │   no OCR in v1               │                  │                  │
                                                                  ▼
[S6] LLM annotation (3 free-tier voters, majority vote) ──► [S7] Convert to BIO + align to tokens
                                                                  ▼
[S8] Human approval (you, in Label Studio) ──► accept / correct / reject
                                                                  ▼
[S9] Corpus build (train/eval BIO + provenance.jsonl + stats)
```

### Stage details

**S1 — Fetch.** `Scrapy` for web (robots-aware, throttled, per-site politeness delay). Board PDFs via direct URLs with resume support; STBB is slow (~40 KB/s) → download sequentially with retries, cache every file. Log every URL, board, class, subject, publisher, fetch date into `sources.sqlite`.

**S2 — Extract (copyable text only).** Web: `trafilatura` (boilerplate-free main text). PDFs: `PyMuPDF` text layer. Any PDF whose text layer is empty or image-based → status `skipped_no_text_layer`, excluded (no OCR in v1). If textbook yield turns out too low, OCR is a documented future option — not built now.

**S3 — Text-layer validation (free heuristics).** Per extracted book:
- Urdu character ratio ≥ 0.7 of letters (Arabic block incl. Urdu extensions);
- **zero Arabic Presentation Forms code points** (U+FB50–U+FDFF, U+FE70–U+FEFF) — their presence is the classic signature of broken Nastaliq extraction;
- sanity checks: common words (کی، کے، نے، میں، اور) present in correct form; no mirrored/reversed variants (e.g., یك);
- sample 3 pages: reject if >10% of lines are garbage.
Fail → status `rejected_bad_text_layer`, recorded for provenance, excluded from the pool.

**S4 — Clean.**
- Unicode NFC; unify Arabic/Persian letter variants (ي→ی, ك→ک, ۀ forms); strip diacritics (اعراب) consistently.
- Remove headers/footers/page numbers, exercise numbering (سوال نمبر), word-list blocks, table-of-contents.
- Keep document → page → sentence provenance chain.
- Poetry detection (short lines + rhyme): mark and **exclude from the NER pool**.

**S5 — Sentence pool.**
- Sentence split on ۔ ؟ ! with post-rules (no `urduhack` — avoids its heavy TF dependency).
- Filters: 5–40 tokens; ≥95% Arabic-script chars, Urdu-specific letters present (پ چ گ ٹ ڈ ڑ ں ہ ی ے) and no Sindhi-only letters (ٽ ٺ ڪ ڏ …) as a free Urdu-vs-Sindhi screen; confirm with `py3langid` on the remainder; not a word-list/table/TOC; not poetry.
- Near-duplicate removal: MinHash (5-gram char shingles, Jaccard ≥ 0.8 → drop) — **essential**: boards reuse stories across classes and each other.
- Stratified sampling targets (per 1,000 sentences): ~55% textbooks (spread across classes & boards), ~35% education news, ~10% university/HEC pages. Log per-stratum counts in the stats report.

---

## 4. LLM Annotation Design (free APIs only — decision #1)

- **Three diverse free voters (all keys verified working 2026-11-04):**
  1. **Groq** — `openai/gpt-oss-120b` (llama-3.3-70b is no longer offered on Groq's free tier; gpt-oss models emit reasoning in a separate field → parse `message.content`, set `reasoning_effort: low`, generous `max_completion_tokens`)
  2. **Google AI Studio** — Gemini flash family with a downshift chain: `gemini-3.8-flash → 3.7-flash → 3.5-flash → 3.1-flash-lite`. Free-tier daily quotas are hit quickly, so 429 downshifts immediately; `thinkingBudget: 0` cuts batch latency from ~120 s to ~4 s. flash-lite is the reliable floor.
  3. **Groq (second model)** — `openai/gpt-oss-20b`. Replaced OpenRouter as the default third voter (2026-11-04): OpenRouter's free lineup rotates and is currently 429/truncated, while gpt-oss-20b answers in ~1 s. OpenRouter stays available via `--voters` as an optional fourth voter.
- Optional 4th voter if a free Mistral key is added later (console.mistral.ai).
- **Batching:** 10–20 sentences per request (JSON array in → JSON array out). ~15K sentences × 3 voters ≈ 2,300–4,500 requests total — comfortably within free-tier daily limits, with retry/downshift/backoff queues.
- **Prompt:** few-shot (10–15 annotated examples covering all 10 tags, plus tricky negatives — استاد as common noun vs. title, LOC vs. PLACE vs. ORG, DATE vs. EVENT), schema-constrained JSON output: `[{sentence_id, entities: [{text, start, end, tag}]}]`.
- **Title-vs-name guideline (settle in pilot):** first tests show voters disagree — Gemini tags "استاد احمد رضا" as one PER span, Groq drops the title. Rule adopted: honorific/role word → `JOBTITLE`, personal name → `PER` (استاد احمد رضا → JOBTITLE + PER). Encoded in the few-shot examples; pilot verifies.
- **Synthetic entities (PHONE, CNIC):** injected into pool sentences *before* annotation (§5b). Their offsets are known exactly, so they are **gold by construction** — voter spans overlapping them are dropped and the gold span is forced in. Voters still see them, so we also record whether each model detected them (a free robustness signal).
- **Self-consistency:** each sentence annotated by all 3 voters independently; entity kept if ≥2/3 agree on span+tag. Record `agreement = 3/3 | 2/3 | 0` per sentence — drives the review sampling (§6).
- **Convert** spans → token-level BIO using the same tokenizer as the corpus files; any span that doesn't align to token boundaries is flagged for review rather than silently clipped.

---

## 5. Tag Schema (10 + O)

Final label set (user-specified, Nov 2026). ZUNERA's 23 tags collapse into it via a fixed mapping, so the two corpora can be merged/compared.

| Our tag | Meaning | ZUNERA source tags | Examples |
|---|---|---|---|
| `PER` | Person name (no titles attached) | PER | علامہ اقبال، سارہ، احمد رضا |
| `LOC` | Geo-political / administrative location: country, province, division, district, city, town, village, region | LOC + GPE | پاکستان، سندھ، کراچی، ضلع ٹھٹھہ |
| `PLACE` | Physical place / facility / generic place noun: building, campus, hospital, park, road, bridge, mountain, river, market | PLACE | سول اسپتال، اسکول کی عمارت، فیصل مسجد، کے ٹو، ایم اے جناح روڈ |
| `JOBTITLE` | Job title / designation / role used as a title | JOBTITLE | **استاد، پروفیسر، پرنسپل، لیکچرار، ڈاکٹر، وزیرِ تعلیم** ← the teacher signal |
| `ORG` | Named organization / institution / body / company / party / media | ORG | سندھ ٹیکسٹ بک بورڈ، جامعۂ کراچی، محکمۂ تعلیم، اقوام متحدہ |
| `TIME` | Clock time, part of day, duration, month/year used as a time reference | TIME | صبح، شام، 8 بجے، دوپہر، ایک گھنٹہ، مارچ میں، 2024 میں |
| `DAY` | Calendar-day expression: weekday name, date | DATE + DAY | پیر، جمعرات، 14 اگست، یکم جنوری |
| `EVENT` | Named event / occasion / observance | EVENT | عید الاضحیٰ، یومِ آزادی، عالمی یومِ اساتذہ، اولمپکس |
| `PHONE` | Phone number — **synthetic only** (§5b) | — | 0300-1234567، +92-321-9876543 |
| `CNIC` | National ID number — **synthetic only** (§5b) | — | 42101-1234567-1 |
| `O` | Everything else (incl. plain numbers, money, quantities, languages, works of art, colors) | CARDINAL, ORDINAL, MONEY, PERCENT, QUANTITY, PRODUCT, WORK_OF_ART, LAW, LANGUAGE, FOOD, DISEASE, NORP, COLOR, MISC | 35 طالب علم، پچاس روپے، اردو |

Boundary/ambiguity rules (also encoded in the few-shot prompt and `docs/LABEL_GUIDELINES.md`):

1. **Title vs. name:** role/honorific word → `JOBTITLE`; the name → `PER` (استاد احمد رضا → `استاد`=JOBTITLE, `احمد رضا`=PER).
2. **LOC vs. PLACE:** administrative/geo-political unit → `LOC`; physical place, facility, or geographic feature → `PLACE`.
3. **PLACE vs. ORG:** named institution acting as an entity/agent (employer, authority, organizer) → `ORG`; the same name used as a physical location ("سول اسپتال جانے کا راستہ") → `PLACE`.
4. **DAY vs. EVENT:** the calendar date → `DAY`; the holiday/observance itself → `EVENT` ("14 اگست کو یومِ آزادی منایا جاتا ہے" → DAY + EVENT).
5. **JOBTITLE is first-class** (not folded into PER/MISC) — it is what makes the corpus serve the teacher/education use case while remaining domain-neutral.
6. Generic institution words with no name (اسکول، کالج، یونیورسٹی as common nouns) → `O`; only named/specific mentions are tagged.

### 5b. Synthetic PHONE & CNIC injection

Phone numbers and CNICs are **not scraped** — they are generated and injected into pool sentences before annotation, so every instance is guaranteed valid-format, non-personal, and exactly labeled.

- **Generators** (`synth/inject.py`): Pakistani mobile/landline formats `03XX-XXXXXXX`, `+92-3XX-XXXXXXX`, `0XX-XXXXXXX`; CNIC `XXXXX-XXXXXXX-X` (5-7-1 digits, e.g. `42101-1234567-1`). Seeded RNG → reproducible; no real number is ever emitted (reserved/fictional ranges, documented in the generator).
- **Injection:** a natural carrier clause is appended to a sampled subset of pool sentences, e.g. `…۔ مزید معلومات کے لیے 0301-2345678 پر رابطہ کریں۔` or `…۔ شناختی کارڈ نمبر 42101-1234567-1 درکار ہے۔` Default rate: **8% PHONE + 8% CNIC per sentence** (configurable per run). At the 5K floor that yields ≈400 of each; raise the rate to 15–20% per type (or extend to 10K sentences) to reach **≥ 1,000 instances of each**.
- **Gold by construction:** injection returns exact character offsets; those spans are marked `synthetic: true` and forced into the gold annotation (voter output overlapping them is discarded). Voter detection of synthetic spans is logged separately as a robustness metric.
- **Distribution note (state in any writeup):** synthetic entities are template-context, not natural web/textbook usage — report metrics for PHONE/CNIC separately from the 8 natural tags.

---

## 6. Approval Workflow (you are the sole reviewer — Label Studio, decision #2)

**Tooling:** Label Studio (Docker, one command), pre-loaded with token offsets + LLM pre-annotations. Entities rendered highlighted; you approve or fix visually.

### Pilot (first 500 sentences) — you review 100%
1. Annotate 500 via §4 pipeline.
2. You review every sentence; every correction is logged (`review_log.jsonl`: sentence_id, original spans, corrected spans, action, timestamp).
3. After 100 and after 300: we compute your agreement with the LLM (Cohen's κ, span-level F1), read every correction, and revise the prompt/guidelines.
4. Gate to proceed: **κ ≥ 0.80 and span-F1 ≥ 0.90** between revised LLM output and your corrections on the last 200 reviewed. Below that → revise prompt, re-annotate, re-review.

### Production (batches of ~1,000) — risk-based sampling
You review:
- **100%** of sentences with any voter disagreement (agreement = 2/3 or lower), and
- a **20% random sample** of unanimous (3/3) sentences.

**Batch acceptance:** ≥95% of your sampled unanimous sentences need zero corrections, and your κ vs. LLM on the batch ≥ 0.80. If a batch fails → fix prompt/guidelines, re-annotate that batch, re-review. Corrections you make are always applied, regardless of batch outcome.

**Your time budget:** pilot ≈ 4–6 h total (spread over a week); each production batch ≈ 1–1.5 h.

**Known limitation (state it in any writeup):** single human annotator ⇒ true inter-annotator agreement isn't possible; we report human–LLM agreement instead. If a second annotator (supervisor/colleague) becomes available for a 200-sentence overlap set, we add real IAA.

---

## 7. Deliverables

```
Urdu-NER-Scraper/
├── PLAN.md                      ← this file
├── docs/LABEL_GUIDELINES.md     ← tag definitions + ambiguity log (source of truth)
├── sources.sqlite               ← every document: url/board/class/subject/publisher/fetch date/status
├── scraper/                     ← web scrapers + board PDF fetchers
├── textbooks/                   ← PDF text extraction + validation heuristics + OCR
├── clean/                       ← normalization, filters, dedup, sentence pool
├── annotate/                    ← schema, prompts, LLM voters (Groq/Gemini), voting, BIO conversion
├── synth/                       ← synthetic PHONE/CNIC generation + injection
├── review/                      ← Label Studio config, review-log tooling, agreement metrics
├── data/
│   ├── pdfs/                    ← cached board PDFs (gitignored)
│   ├── ocr/                     ← per-page OCR cache + book.txt (gitignored)
│   ├── pool/                    ← filtered sentence pool (jsonl + provenance ids)
│   ├── annotations/             ← voter output per batch (pilot.jsonl, batch_NN.jsonl)
│   ├── ls/                      ← Label Studio import files + exports (gitignored)
│   └── education_v1/
│       ├── train.bio            ← approved data (CoNLL BIO2)
│       ├── eval.bio             ← held-out approved data
│       ├── review_log.jsonl     ← every human correction
│       ├── provenance.jsonl     ← sentence_id → source, board, class, url, validation status
│       └── REPORT.md            ← counts, strata, agreement stats, correction rates
```

`provenance.jsonl` means every corpus sentence is traceable to its board/class/URL — required for any publication.

---

## 8. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Broken Urdu ligature order from PDF text layers | S3 heuristics (Presentation-Forms check + sanity bigrams); reject & skip; OCR revisited only if yield too low |
| Textbook yield (copyable-text only) | **Superseded:** free-tier Gemini OCR adopted (validated on a real STBB page, §9). Residual risk: OCR noise on poems/tables → same S3–S5 validation + human review sampling applies to OCR output |
| Free-tier rate limits throttle annotation | Batch 10–20 sentences/request; retry/backoff queues across 3 providers; expected ~1–2K sentences/day |
| PCTB under maintenance / NBF blocks bots | Aggregator mirrors (taleem360, ustad360) with URL logging; STBB verified & sufficient to start |
| Copyright on board textbooks | Boards publish PDFs free for education; research use with attribution; **we store annotated sentences + provenance, never redistribute full books**; before publishing, email STBB/PCTB/NBF for written permission |
| LLM noise on Urdu names/spans | 3-model voting, few-shot prompt, disagreement sentences go 100% to you, κ gates |
| Duplicate stories across boards/classes | MinHash dedup with Jaccard ≥ 0.8 |
| Reviewer fatigue (you) | Risk-based sampling (§6), batches gated so rework is small |
| Schema drift mid-project | Schema (§5) frozen before pilot; changes only via re-annotation, logged in REPORT.md |
| ZUNERA repo has no license | Already flagged; email authors before merging anything with ZUNERA |

---

## 9. Decisions — locked ✅

| # | Question | Decision |
|---|---|---|
| 1 | Annotation LLMs | **Free APIs only** → Groq `openai/gpt-oss-120b` + Gemini flash chain + OpenRouter free tier (3 diverse voters), batched 10–20 sentences/request (verified live 2026-11-04) |
| 2 | Review tool | **Label Studio** |
| 3 | Corpus size | **Maximize** within free-tier throughput; pool 15–20K, approve as much as possible, floor 5K approved |
| 4 | Grades | **All classes Kachi–XII**; sampling weighted toward Class 4–12 (grades = جماعت/classes, i.e., which class's textbook a sentence comes from) |
| 5 | OCR | **Adopted** (superseded 2026-11-04) — all board PDFs are image-only; free-tier Gemini OCR validated, §9 below |
| 6 | Tag schema | **10 labels + O** (§5): PER, LOC, PLACE, JOBTITLE, ORG, TIME, DAY, EVENT, PHONE, CNIC |
| 7 | PHONE / CNIC | **Synthetic** — generated and injected before annotation, gold by construction (§5b); never scraped |

**✅ Resolved (2026-11-04): free-tier Gemini OCR adopted.** User supplied a working AI Studio key. OCR quality **validated on a real STBB Urdu Reader IV page**: near-perfect Nastaliq transcription incl. diacritics and proper nouns, via `gemini-3.1-flash-lite`. Free tier ≈ 1,500 requests/day ≈ **7–10 books/day**. Pipeline: PDF → page images (pymupdf, 150 dpi) → Gemini transcribe → same S3–S5 validation/cleaning as native text. The "copyable-text only" constraint is superseded — textbooks are back in scope at $0.

---

## 10. Timeline

| Week | Milestone |
|---|---|
| 1 | Repo scaffolding; source registry; STBB Urdu books fetched (PCTB/NBF via mirrors); extraction + validation harness passing |
| 2 | Cleaning + dedup; sentence pool; pilot 500 sampled & pre-annotated |
| 3 | Pilot review (you, 100%); prompt v2; pilot gate (κ ≥ 0.80) |
| 4 | Production batches with risk-based approval (throughput limited by free tiers) |
| 5 | Corpus freeze at whatever approved volume reached (≥5K); stats report; first XLM-R fine-tune on ZUNERA evaluated on education_v1 |
