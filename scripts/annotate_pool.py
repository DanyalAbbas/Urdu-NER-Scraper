#!/usr/bin/env python3
"""Annotate the sentence pool with 3-voter majority + synthetic PHONE/CNIC.

Examples:
    # pilot: 500 sentences, stratified across docs, synthetic injection on
    uv run python scripts/annotate_pool.py --limit 500

    # smoke test: 20 sentences with a single voter
    uv run python scripts/annotate_pool.py --limit 20 --voters groq --out data/annotations/smoke.jsonl

    # resume an interrupted run (ok records are skipped, incomplete retried)
    uv run python scripts/annotate_pool.py --limit 500
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from annotate import voters  # noqa: E402
from synth.inject import inject  # noqa: E402


def load_pool(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    done: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("status") == "ok":
            done.add(rec["sentence_id"])
    return done


def _round_robin(rows: list[dict], limit: int, rng: random.Random, max_per_doc: int,
                 textbook_cap: int = 400) -> list[dict]:
    """Round-robin across documents. Web articles are capped low (no single
    article dominates); the OCR book is one document holding all textbook
    sentences, so it gets a much higher cap."""
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_doc[r["doc_id"]].append(r)
    for doc_rows in by_doc.values():
        rng.shuffle(doc_rows)

    doc_ids = sorted(by_doc)
    rng.shuffle(doc_ids)
    out: list[dict] = []
    cursor = {d: 0 for d in doc_ids}
    while len(out) < limit:
        progressed = False
        for doc_id in doc_ids:
            i = cursor[doc_id]
            cap = textbook_cap if doc_id.startswith("ocr_") else max_per_doc
            if i >= len(by_doc[doc_id]) or i >= cap:
                continue
            out.append(by_doc[doc_id][i])
            cursor[doc_id] = i + 1
            progressed = True
            if len(out) >= limit:
                break
        if not progressed:
            break
    return out


def sample_pool(rows: list[dict], limit: int, seed: int, max_per_doc: int = 25,
                textbook_share: float = 0.6) -> list[dict]:
    """Stratified sample: ~60% textbook, ~40% web (PLAN.md §3), round-robin
    within each stratum so no single document dominates."""
    rng = random.Random(seed)
    textbook = [r for r in rows if r["source"].startswith("ocr_")]
    web = [r for r in rows if not r["source"].startswith("ocr_")]

    want_tb = min(round(limit * textbook_share), len(textbook))
    want_web = min(limit - want_tb, len(web))
    # backfill if one stratum is short
    want_tb = min(len(textbook), limit - want_web)

    picked = _round_robin(textbook, want_tb, rng, max_per_doc)
    picked += _round_robin(web, want_web, rng, max_per_doc)
    rng.shuffle(picked)
    return picked


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pool", default="data/pool/pool.jsonl")
    ap.add_argument("--out", default="data/annotations/pilot.jsonl")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--batch", type=int, default=8, help="sentences per request")
    ap.add_argument("--pace", type=float, default=12.0, help="seconds between batches")
    ap.add_argument("--voters", default="groq,gemini,groq2")
    ap.add_argument("--phone-prob", type=float, default=0.08)
    ap.add_argument("--cnic-prob", type=float, default=0.08)
    ap.add_argument("--no-inject", action="store_true")
    ap.add_argument("--max-per-doc", type=int, default=25)
    ap.add_argument("--textbook-share", type=float, default=0.6,
                    help="fraction of the sample drawn from textbook OCR")
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()

    pool_path, out_path = Path(args.pool), Path(args.out)
    rows = load_pool(pool_path)
    print(f"{len(rows)} pool sentences from {pool_path}")

    done = set() if args.no_resume else load_done(out_path)
    if done:
        rows = [r for r in rows if r["sentence_id"] not in done]
        print(f"resuming: {len(done)} already annotated, {len(rows)} candidates left")

    sampled = sample_pool(rows, args.limit, args.seed, max_per_doc=args.max_per_doc,
                          textbook_share=args.textbook_share)
    print(f"sampled {len(sampled)} sentences across {len({r['doc_id'] for r in sampled})} docs")

    rng = random.Random(args.seed)
    items: list[dict] = []
    n_phone = n_cnic = 0
    for r in sampled:
        gold: list[dict] = []
        text = r["text"]
        if not args.no_inject:
            text, gold = inject(
                r["text"], rng,
                phone_prob=args.phone_prob, cnic_prob=args.cnic_prob,
            )
        n_phone += sum(1 for g in gold if g.tag == "PHONE")
        n_cnic += sum(1 for g in gold if g.tag == "CNIC")
        items.append({
            "sentence_id": r["sentence_id"],
            "doc_id": r["doc_id"],
            "source": r["source"],
            "provenance": r.get("provenance", ""),
            "text": text,
            "base_text": r["text"],
            "gold_spans": [g.to_dict() for g in gold],
        })
    print(f"synthetic injection: {n_phone} PHONE, {n_cnic} CNIC spans")

    voter_names = [v.strip() for v in args.voters.split(",") if v.strip()]
    print(f"annotating with {voter_names} (batch={args.batch}, pace={args.pace}s)")

    records = voters.annotate_items(
        items, batch_size=args.batch, pace=args.pace, voter_names=voter_names,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    ok = [r for r in records if r.get("status") == "ok"]
    print(f"\nwrote {len(records)} records -> {out_path} ({len(ok)} ok, "
          f"{len(records) - len(ok)} incomplete)")
    if ok:
        print("agreement:", json.dumps(dict(Counter(r["agreement"] for r in ok))))
        tags = Counter(s["tag"] for r in ok for s in r["spans"] if not s["synthetic"])
        print("natural entity counts:", json.dumps(dict(tags.most_common())))
        synth = Counter(s["tag"] for r in ok for s in r["spans"] if s["synthetic"])
        print("synthetic entity counts:", json.dumps(dict(synth)))
        det = Counter(v for r in ok for v in r.get("synthetic_detected", {}).values())
        print("synthetic detected by voters:", json.dumps(dict(det)))


if __name__ == "__main__":
    main()
