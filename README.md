# Urdu-NER-Scraper

Education-domain Urdu NER corpus builder: scrape Pakistani textbook-board books and
education web pages, annotate with free-tier LLMs (3-voter majority), human-approve in
Label Studio, and emit a provenance-tracked CoNLL/BIO corpus.

**Start here → [`PLAN.md`](PLAN.md)** — full plan with locked decisions (§9), pipeline,
schema, approval workflow, and timeline.

Quick context:
- Primary general-domain training data: [ZUNERA](https://github.com/zainab869/ZUNERA)
  (1.19M tokens, 23 tags; paper: [Urdu-NERD, PeerJ CS 2026](https://peerj.com/articles/cs-3678/)).
- This repo produces the complementary education-domain slice (~5K+ approved sentences)
  used for domain evaluation and optional fine-tuning.

## Setup

Uses [`uv`](https://docs.astral.sh/uv/) for dependency management (`pyproject.toml` + `uv.lock`):

```bash
uv sync              # creates .venv and installs everything
cp .env.example .env # fill in free-tier API keys
uv run python ...    # or: source .venv/bin/activate
```

## Code layout

```
scraper/stbb.py        STBB portal client: list Urdu-medium books, resumable PDF download
scraper/validate.py    text-layer validation (TEXT_OK / TEXT_BROKEN / IMAGE_ONLY / UNREADABLE)
scripts/survey_stbb.py CLI: probe STBB books for copyable text, writes JSON report
```

## Status

- [x] Plan v1.1 (decisions locked)
- [x] STBB text-layer survey — **conclusive: all 4 Pakistani textbook pipelines are image-only** (STBB 26/26 books; PECTAA/Punjab CorelDRAW; FBISE/NBF Ghostscript; AIOU scanned). Copyable-only ⇒ 0 textbook data. Open decision: web-first + free-tier Gemini OCR (PLAN.md §9).
- [x] Source registry + STBB scraper/validator code
- [ ] PCTB/NBF (via taleem360/ustad360 mirrors) — pending; official PCTB portal was under maintenance
- [ ] Cleaning + sentence pool
- [ ] LLM voters + BIO conversion
- [ ] Label Studio approval workflow
- [ ] Corpus v1.0
