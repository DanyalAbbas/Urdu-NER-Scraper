#!/usr/bin/env python3
"""Label Studio export -> approved BIO corpus + review log + agreement stats.

Usage:
    uv run python scripts/ls_to_corpus.py --export data/ls/export_pilot.json \
        --annotations data/annotations/pilot.jsonl \
        --out-bio data/education_v1/pilot.bio

Every reviewed sentence becomes a CoNLL/BIO2 record. Sentences where the human
changed nothing are 'approved'; changed ones are 'edited' and the diff is
logged. Rejected/cancelled annotations are skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from annotate.voters import _align, tokenize  # noqa: E402
from review.metrics import cohen_kappa, span_prf, spans_to_token_labels  # noqa: E402


def char_spans_to_token_spans(text: str, spans: list[dict]) -> set[tuple[int, int, str]]:
    tokens = tokenize(text)
    out: set[tuple[int, int, str]] = set()
    for s in spans:
        aligned = _align(tokens, int(s["start"]), int(s["end"]))
        if aligned:
            i0, i1, _ = aligned
            out.add((i0, i1, s["tag"]))
    return out


def ls_results_to_spans(results: list[dict]) -> list[dict]:
    spans = []
    for r in results:
        if r.get("type") != "labels":
            continue
        v = r.get("value", {})
        labels = v.get("labels") or []
        if not labels:
            continue
        spans.append({"start": v["start"], "end": v["end"], "tag": labels[0]})
    return spans


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--export", required=True, help="Label Studio JSON export")
    ap.add_argument("--annotations", default="data/annotations/pilot.jsonl",
                    help="original voter output (for agreement vs LLM)")
    ap.add_argument("--out-bio", default="data/education_v1/pilot.bio")
    ap.add_argument("--out-log", default="data/education_v1/review_log.jsonl")
    ap.add_argument("--out-provenance", default="data/education_v1/provenance.jsonl")
    args = ap.parse_args()

    tasks = json.loads(Path(args.export).read_text(encoding="utf-8"))
    original = {}
    ann_path = Path(args.annotations)
    if ann_path.exists():
        for line in ann_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                original[rec["sentence_id"]] = rec

    bio_lines: list[str] = []
    log_lines: list[dict] = []
    prov_lines: list[dict] = []
    kappas: list[float] = []
    prf_tp = prf_pred = prf_gold = 0
    n_approved = n_edited = n_skipped = 0

    for task in tasks:
        data = task.get("data", {})
        sid = data.get("sentence_id") or str(task.get("id"))
        text = data.get("text", "")
        annotations = [a for a in task.get("annotations", [])
                       if not a.get("was_cancelled")]
        if not annotations:
            n_skipped += 1
            continue
        ann = annotations[-1]
        human_spans = ls_results_to_spans(ann.get("result", []))
        human_tok = char_spans_to_token_spans(text, human_spans)

        tokens = tokenize(text)
        labels = spans_to_token_labels(len(tokens), human_tok)
        for tok, lab in zip(tokens, labels):
            bio_lines.append(f"{tok.text}\t{lab}")
        bio_lines.append("")

        rec = original.get(sid, {})
        llm_spans = char_spans_to_token_spans(
            text, [{"start": s["start"], "end": s["end"], "tag": s["tag"]}
                   for s in rec.get("spans", []) if not s.get("synthetic")])
        llm_labels = spans_to_token_labels(len(tokens), llm_spans)

        kappas.append(cohen_kappa(llm_labels, labels))
        prf = span_prf(llm_spans, human_tok)
        prf_tp += prf["tp"]
        prf_pred += prf["n_pred"]
        prf_gold += prf["n_gold"]

        action = "approved" if llm_spans == human_tok else "edited"
        n_approved += action == "approved"
        n_edited += action == "edited"

        log_lines.append({
            "sentence_id": sid,
            "action": action,
            "llm_spans": sorted([{"i0": a, "i1": b, "tag": c} for a, b, c in llm_spans],
                                key=lambda x: x["i0"]),
            "human_spans": sorted([{"i0": a, "i1": b, "tag": c} for a, b, c in human_tok],
                                  key=lambda x: x["i0"]),
            "agreement": rec.get("agreement", ""),
            "reviewer": ann.get("completed_by"),
            "timestamp": time.time(),
        })
        prov_lines.append({
            "sentence_id": sid,
            "source": data.get("source", ""),
            "doc_id": data.get("doc_id", ""),
            "provenance": data.get("provenance", ""),
            "llm_agreement": rec.get("agreement", ""),
            "review_action": action,
        })

    out_bio = Path(args.out_bio)
    out_bio.parent.mkdir(parents=True, exist_ok=True)
    out_bio.write_text("\n".join(bio_lines) + "\n", encoding="utf-8")

    out_log = Path(args.out_log)
    out_log.parent.mkdir(parents=True, exist_ok=True)
    with out_log.open("w", encoding="utf-8") as f:
        for row in log_lines:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    out_prov = Path(args.out_provenance)
    out_prov.parent.mkdir(parents=True, exist_ok=True)
    with out_prov.open("w", encoding="utf-8") as f:
        for row in prov_lines:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    reviewed = len(log_lines)
    micro_p = prf_tp / prf_pred if prf_pred else 1.0
    micro_r = prf_tp / prf_gold if prf_gold else 1.0
    micro_f1 = 2 * micro_p * micro_r / (micro_p + micro_r) if (micro_p + micro_r) else 0.0
    mean_kappa = sum(kappas) / len(kappas) if kappas else 0.0

    print(f"reviewed {reviewed} sentences ({n_skipped} skipped)")
    print(f"  approved (no edits): {n_approved}  |  edited: {n_edited}")
    print(f"  mean token-level Cohen's kappa vs LLM: {mean_kappa:.3f}")
    print(f"  span-level micro P/R/F1 vs LLM: {micro_p:.3f} / {micro_r:.3f} / {micro_f1:.3f}")
    print(f"wrote {out_bio}, {out_log}, {out_prov}")
    gate = mean_kappa >= 0.80 and micro_f1 >= 0.90
    print(f"pilot gate (kappa>=0.80 and span-F1>=0.90): {'PASS' if gate else 'FAIL'}")


if __name__ == "__main__":
    main()
