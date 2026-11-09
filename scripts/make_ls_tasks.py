#!/usr/bin/env python3
"""Convert voter annotations into Label Studio import tasks with predictions.

Usage:
    uv run python scripts/make_ls_tasks.py --annotations data/annotations/pilot.jsonl
    uv run python scripts/make_ls_tasks.py --annotations data/annotations/pilot.jsonl \
        --only-disagreements

Then in Label Studio: Import -> data/ls/tasks_pilot.json (tasks arrive pre-annotated).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def to_ls_task(rec: dict) -> dict:
    results = []
    for span in rec.get("spans", []):
        results.append({
            "from_name": "label",
            "to_name": "text",
            "type": "labels",
            "value": {
                "start": span["start"],
                "end": span["end"],
                "text": span["text"],
                "labels": [span["tag"]],
            },
        })
    n = max(len(rec.get("spans", [])), 1)
    score = sum(1 for s in rec.get("spans", []) if s.get("votes", 0) >= 2) / n
    return {
        "data": {
            "text": rec["text"],
            "sentence_id": rec["sentence_id"],
            "source": rec.get("source", ""),
            "doc_id": rec.get("doc_id", ""),
            "agreement": rec.get("agreement", ""),
            "provenance": rec.get("provenance", ""),
        },
        "predictions": [{
            "model_version": "llm-3voter-v1",
            "score": round(score, 3),
            "result": results,
        }],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--annotations", default="data/annotations/pilot.jsonl")
    ap.add_argument("--out", default="data/ls/tasks_pilot.json")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only-disagreements", action="store_true",
                    help="import only sentences with 2/3 or lower agreement")
    ap.add_argument("--include-incomplete", action="store_true")
    args = ap.parse_args()

    recs = [json.loads(line) for line in Path(args.annotations)
            .read_text(encoding="utf-8").splitlines() if line.strip()]
    if not args.include_incomplete:
        recs = [r for r in recs if r.get("status") == "ok"]
    if args.only_disagreements:
        recs = [r for r in recs if r.get("agreement") != "3/3"]
    if args.limit:
        recs = recs[:args.limit]

    tasks = [to_ls_task(r) for r in recs]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")

    n_spans = sum(len(t["predictions"][0]["result"]) for t in tasks)
    print(f"wrote {len(tasks)} tasks ({n_spans} pre-annotated spans) -> {out}")
    print("Import into Label Studio, review, then export JSON and run ls_to_corpus.py")


if __name__ == "__main__":
    main()
