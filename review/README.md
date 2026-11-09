# Label Studio review workflow

Human approval is the gate for the whole corpus (PLAN.md §6). Label Studio is
the only tool you need to run.

## 1. Start Label Studio

```bash
mkdir -p data/ls
docker run -it --rm -p 8080:8080 \
  -v "$(pwd)/data/ls:/label-studio/data" \
  heartexlabs/label-studio:latest
```

Open <http://localhost:8080> and create the first account (local only).

## 2. Create the project

1. **Create Project** → name it `urdu-ner-pilot`.
2. **Labeling Setup** → *Code* tab → paste the contents of
   [`review/label_config.xml`](label_config.xml).
3. **Import** → upload `data/ls/tasks_pilot.json` (generated below).
   The LLM pre-annotations arrive as predictions; you only fix what is wrong.

Generate the import file:

```bash
uv run python scripts/make_ls_tasks.py --annotations data/annotations/pilot.jsonl
# disagreements only (production batches):
uv run python scripts/make_ls_tasks.py --annotations data/annotations/batch_01.jsonl \
    --only-disagreements --out data/ls/tasks_batch_01.json
```

## 3. Review

- **No edit = approved.** Submitting the pre-annotation unchanged is an approval.
- Fix spans/tags in place. RTL text is styled in the config.
- Leave a task unsubmitted to skip it (it is excluded from the corpus).
- Pilot: review **all 500**. Production: 100% of `2/3` sentences + 20% random
  sample of `3/3` (see PLAN.md §6).

## 4. Export and build the corpus

**Export** → JSON → save as `data/ls/export_pilot.json`, then:

```bash
uv run python scripts/ls_to_corpus.py \
  --export data/ls/export_pilot.json \
  --annotations data/annotations/pilot.jsonl \
  --out-bio data/education_v1/pilot.bio
```

Outputs:

| File | Contents |
|---|---|
| `data/education_v1/pilot.bio` | approved CoNLL/BIO2 corpus (token TAB tag, blank line per sentence) |
| `data/education_v1/review_log.jsonl` | per-sentence diff: LLM spans vs your spans, action, timestamp |
| `data/education_v1/provenance.jsonl` | sentence_id → source, doc, URL, review action |

The script prints the gate numbers: **mean token-level Cohen's κ** and
**span-level micro F1** against the LLM. Gate to scale: κ ≥ 0.80 and F1 ≥ 0.90.

## Notes

- `PHONE` / `CNIC` spans are synthetic and gold by construction; they are
  pre-annotated like everything else, but edits are only needed if injection
  itself looks malformed.
- If a sentence is OCR garbage, delete all its spans and still submit — or
  skip it. Skipped sentences never enter the corpus.
- Re-running `make_ls_tasks.py` is safe; sentence IDs are stable.
