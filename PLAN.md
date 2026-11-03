# Urdu Education-Domain Corpus: Scraping, LLM Annotation & Approval Plan

**Status:** v1.1 — decisions locked (§9). Ready to scaffold.
**Goal:** Build an education-domain Urdu NER corpus (textbook + education-web text), annotated via free-tier LLMs with human approval, to complement ZUNERA (primary general-domain training set, 1.19M tokens). Final use: domain evaluation set + optional fine-tuning slice. This is the repurposed "Option A" pipeline from `Urdu_NER_Dataset_Report.pdf`, narrowed per the ZUNERA findings.

---

## 1. Scope & Targets

| Item | Decision |
|---|---|
| Domain | Education (textbooks, education news). General domain is already covered by ZUNERA. |
| Sources | Textbook board PDFs (copyable-text only) + education web pages |
| Pilot size | **500 sentences** — 100% human-reviewed before scaling |
| Corpus size | **Maximize** within free-API throughput: pool 15–20K sentences, approve as much as possible; hard floor **5,000 approved sentences** |
| Tag schema | 8 tags + `O` (see §5) — aligned with ZUNERA's 23-tag set via fixed mapping |
| Format | CoNLL/BIO2 (token TAB tag, blank line = sentence boundary), one file per split |
| Golden rule | LLM output is never the gold standard. Every sentence in the final corpus is human-approved. |

---

## 2. Data Sources

### 2a. Textbook boards (all free; verified Nov 2026)

| Board | Portal | Status | How to fetch |
|---|---|---|---|
| Sindh Textbook Board (STBB), Jamshoro | `portal.stbb.edu.pk/ebooks` | ✅ live, but **books are image-only** | Class listing: `class.php?id=…&medium=Urdu` (ids: 17=Kachi, 1=I, 3=II, 4=III, 5=IV, 6=V, 7=VI, 8=VII, 9=VIII, 10=IX, 11=X, 14=XI, 15=XII). Book pages: `book.php?id=…`. PDFs: `pdf_proxy.php?id=…&download=1`. ~10 MB/book at ~40 KB/s → polite concurrency 1–2, resumable range requests. **Survey (Nov 2026): 26 books probed across all classes & years 2020–2026 — every one has zero fonts / zero text layer (exported via PScript→Distiller as page images).** |
| Punjab (PCTB/PECTAA) | `pctb.punjab.gov.pk/E-Books` | ⚠️ under maintenance at plan time | Fallback: `taleem360.com` Punjab section (or revisit official portal later). Log aggregator URLs in provenance |
| Federal (FBISE) via National Book Foundation | `nbf.org.pk/chapters-of-text-books` | ⚠️ blocks automated access | Fallback: `taleem360.com` / `ustad360.com` Federal sections. Log URLs |
| KPK Textbook Board | — | optional later | Same aggregator approach |

**Which books:** Urdu (اردو) readers for every class + Urdu-medium Islamiat (Mazhabi Taleemat), Social Studies (Mashrati Uloom), Science, Civics/Education. These carry stories, essays, biographies, dialogues — the richest NER content. Skip Mathematics problem sets (number lists, no prose).

**Grade coverage (decision #4):** all classes, Kachi → XII. Sampling weighted toward Class 4–12 (longer prose, more named entities); lower classes kept at lower weight — heavy in استاد/اسکول/سبق vocabulary, exactly the teacher signal we want.

**Copyable-text only (decision #5):** only PDFs with a real text layer are used. Scanned/image-only books are detected, recorded, and skipped. No OCR in v1.

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

- **Three diverse free-tier voters** (diversity of models beats 3 runs of one model):
  1. **Groq** — Llama 3.3 70B (free tier, very fast)
  2. **Google AI Studio** — Gemini 2.0 Flash (free tier)
  3. **Mistral La Plateforme** — Mistral Small 3.x (free tier)
- Backups: OpenRouter `:free` models; or local **Ollama** (e.g., Qwen2.5) if a GPU is available — zero cost, no rate limits.
- **Batching:** 10–20 sentences per request (JSON array in → JSON array out). ~15K sentences × 3 voters ≈ 2,300–4,500 requests total — comfortably within free-tier daily limits, with retry/backoff queues.
- **Prompt:** few-shot (10–15 annotated examples covering PER/LOC/ORG/JOBTITLE/DATE and tricky negatives like استاد as common noun vs. title), schema-constrained JSON output: `[{sentence_id, entities: [{text, start, end, tag}]}]`.
- **Self-consistency:** each sentence annotated by all 3 voters independently; entity kept if ≥2/3 agree on span+tag. Record `agreement = 3/3 | 2/3 | 0` per sentence — drives the review sampling (§6).
- **Convert** spans → token-level BIO using the same tokenizer as the corpus files; any span that doesn't align to token boundaries is flagged for review rather than silently clipped.

---

## 5. Tag Schema (8 + O)

Aligned with ZUNERA so the corpora can be merged/compared. Fixed mapping from ZUNERA's 23 tags:

| Our tag | Source tags (ZUNERA) | Examples |
|---|---|---|
| `PER` | PER | علامہ اقبال، سارہ |
| `LOC` | LOC + GPE + PLACE | کراچی، پاکستان، اسکول کی عمارت |
| `ORG` | ORG | بورڈ، جامعہ، HEC |
| `JOBTITLE` | JOBTITLE | **استاد، پروفیسر، پرنسپل، لیکچرار** ← the teacher signal |
| `DATE/TIME` | DATE + DAY + TIME | 14 اگست، پیر، صبح 8 بجے |
| `NUM` | CARDINAL + ORDINAL + MONEY + PERCENT + QUANTITY | 35 طالب علم، پچاس روپے |
| `MISC` | PRODUCT, EVENT, WORK_OF_ART, LAW, LANGUAGE, FOOD, DISEASE, NORP, COLOR | عید، اردو، ٹائیفائیڈ |
| `O` | O | — |

JOBTITLE stays first-class (not folded into MISC) — it is what makes the corpus serve the teacher/education use case while remaining domain-neutral.

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
├── sources.sqlite               ← every document: url/board/class/subject/publisher/fetch date/status
├── scraper/                     ← web scrapers + board PDF fetchers
├── textbooks/                   ← PDF text extraction + validation heuristics
├── clean/                       ← normalization, filters, dedup, sentence pool
├── annotate/                    ← LLM voters (Groq/Gemini/Mistral), batching, voting, BIO conversion
├── review/                      ← Label Studio configs, review-log tooling, agreement metrics
├── data/
│   ├── pdfs/                    ← cached board PDFs (gitignored)
│   ├── pool/                    ← filtered sentence pool (jsonl + provenance ids)
│   ├── batches/                 ← per-batch pre-annotation + review snapshots
│   └── education_v1/
│       ├── train.bio            ← approved data (CoNLL BIO2)
│       ├── eval.bio             ← held-out approved data
│       ├── provenance.jsonl     ← sentence_id → source, board, class, url, validation status
│       └── REPORT.md            ← counts, strata, agreement stats, correction rates
```

`provenance.jsonl` means every corpus sentence is traceable to its board/class/URL — required for any publication.

---

## 8. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Broken Urdu ligature order from PDF text layers | S3 heuristics (Presentation-Forms check + sanity bigrams); reject & skip; OCR revisited only if yield too low |
| Textbook yield (copyable-text only) | **Confirmed zero for STBB** (26/26 books image-only). PCTB/NBF mirrors to be probed; OCR is the fallback (§9 open decision). Web sources unaffected. |
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
| 1 | Annotation LLMs | **Free APIs only** → Groq Llama-3.3-70B + Gemini 2.0 Flash + Mistral Small (3 diverse voters), batched 10–20 sentences/request |
| 2 | Review tool | **Label Studio** |
| 3 | Corpus size | **Maximize** within free-tier throughput; pool 15–20K, approve as much as possible, floor 5K approved |
| 4 | Grades | **All classes Kachi–XII**; sampling weighted toward Class 4–12 (grades = جماعت/classes, i.e., which class's textbook a sentence comes from) |
| 5 | OCR | **Skipped for now** — copyable-text PDFs only; scanned books detected, recorded, excluded |

**⚠️ Open decision (Nov 2026 survey result):** STBB books are 100% image-only (26/26 probed across all classes and years). Under decision #5 this yields **zero textbook data**. Options:
- **(a) Web-first (no plan change):** start with education news + university pages (all have copyable text); revisit textbooks later.
- **(b) Revisit OCR for STBB:** vision-LLM OCR (~$10–30 one-time, best quality) or free Tesseract `urd` (weaker); decision #5 explicitly deferred this.
- **(c) Check PCTB/NBF mirrors first:** taleem360-hosted Punjab/Federal books may carry real text layers (their PDFs often come from different production pipelines) — cheap to test before deciding (a) vs (b).

Recommendation: **(c) then (a)** — probe a handful of PCTB/NBF mirror PDFs for text layers this week; start web scraping regardless; decide on OCR only after seeing mirror results.

---

## 10. Timeline

| Week | Milestone |
|---|---|
| 1 | Repo scaffolding; source registry; STBB Urdu books fetched (PCTB/NBF via mirrors); extraction + validation harness passing |
| 2 | Cleaning + dedup; sentence pool; pilot 500 sampled & pre-annotated |
| 3 | Pilot review (you, 100%); prompt v2; pilot gate (κ ≥ 0.80) |
| 4 | Production batches with risk-based approval (throughput limited by free tiers) |
| 5 | Corpus freeze at whatever approved volume reached (≥5K); stats report; first XLM-R fine-tune on ZUNERA evaluated on education_v1 |
