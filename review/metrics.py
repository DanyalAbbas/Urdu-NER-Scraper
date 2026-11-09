"""Agreement metrics for human-vs-LLM review (pure Python, no sklearn).

Two complementary numbers, as required by PLAN.md §6:
  - token-level Cohen's kappa (BIO labels)
  - span-level micro precision / recall / F1 (exact boundary + tag match)
"""

from __future__ import annotations

from collections import Counter


def spans_to_token_labels(n_tokens: int, spans) -> list[str]:
    """spans: iterable of (i0, i1, tag) with i1 exclusive -> BIO labels."""
    labels = ["O"] * n_tokens
    for i0, i1, tag in sorted(spans):
        labels[i0] = f"B-{tag}"
        for i in range(i0 + 1, min(i1, n_tokens)):
            labels[i] = f"I-{tag}"
    return labels


def cohen_kappa(a: list[str], b: list[str]) -> float:
    """Cohen's kappa for two equal-length label sequences."""
    if len(a) != len(b):
        raise ValueError(f"length mismatch: {len(a)} vs {len(b)}")
    n = len(a)
    if n == 0:
        return 1.0
    observed = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb.get(k, 0) for k in ca) / (n * n)
    if expected >= 1.0:
        return 1.0
    return (observed - expected) / (1.0 - expected)


def span_prf(gold: set[tuple[int, int, str]],
             pred: set[tuple[int, int, str]]) -> dict:
    """Micro P/R/F1 over exact (start_tok, end_tok, tag) matches."""
    tp = len(gold & pred)
    precision = tp / len(pred) if pred else (1.0 if not gold else 0.0)
    recall = tp / len(gold) if gold else (1.0 if not pred else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tp": tp,
        "n_gold": len(gold),
        "n_pred": len(pred),
    }


def entity_confusion(gold: set[tuple[int, int, str]],
                     pred: set[tuple[int, int, str]]) -> dict[str, dict[str, int]]:
    """Boundary matches with different tags: {(gold_tag, pred_tag): count}."""
    by_span_gold = {(i0, i1): tag for i0, i1, tag in gold}
    by_span_pred = {(i0, i1): tag for i0, i1, tag in pred}
    conf: Counter = Counter()
    for span, gtag in by_span_gold.items():
        ptags = [ptag for (i0, i1), ptag in by_span_pred.items() if (i0, i1) == span]
        for ptag in ptags:
            if ptag != gtag:
                conf[(gtag, ptag)] += 1
    return {f"{g}->{p}": c for (g, p), c in conf.most_common()}
