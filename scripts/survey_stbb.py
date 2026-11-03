#!/usr/bin/env python3
"""Survey STBB Urdu-medium books: which have a copyable text layer?

Downloads the first ~1 MB of each sampled book (ranged request) and runs
scraper.validate.probe_pdf on it. Writes a JSON report and prints a table.

Usage:
    uv run python scripts/survey_stbb.py                 # full survey
    uv run python scripts/survey_stbb.py --limit 4      # smoke test
    uv run python scripts/survey_stbb.py --out data/survey_stbb.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scraper import stbb, validate  # noqa: E402

#: per class: prefer an Urdu reader, then one prose-heavy subject book (skip math)
_SKIP_RE = re.compile(r"riazi|math|hisab", re.I)
_READER_RE = re.compile(r"urdu", re.I)


def pick_books(books: list[stbb.Book], per_class: int) -> list[stbb.Book]:
    readers = [b for b in books if _READER_RE.search(b.title)]
    others = [b for b in books if not _SKIP_RE.search(b.title)]
    picks: list[stbb.Book] = []
    for pool in (readers, others):
        for b in pool:
            if len(picks) >= per_class:
                return picks
            if b.id not in {p.id for p in picks}:
                picks.append(b)
    return picks


def probe(book: stbb.Book, workdir: Path, max_mb: float) -> dict:
    dest = workdir / f"book_{book.id}.pdf"
    size = stbb.download_pdf(book.id, str(dest), resume=True, max_bytes=int(max_mb * 1e6))
    result = validate.probe_pdf(str(dest))
    out = {"book": book.to_dict(), "downloaded_bytes": size, **result.to_dict()}
    # partial probe files are throwaway — keep the disk clean
    if result.verdict != "TEXT_OK" or max_mb < 5:
        dest.unlink(missing_ok=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-class", type=int, default=2, help="books to sample per class")
    ap.add_argument("--limit", type=int, default=0, help="probe at most N books (0 = all)")
    ap.add_argument("--max-mb", type=float, default=1.0, help="MB to download per book")
    ap.add_argument("--workdir", default="data/survey_pdfs")
    ap.add_argument("--out", default="data/survey_stbb.json")
    args = ap.parse_args()

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("listing Urdu-medium books per class...")
    picks: list[stbb.Book] = []
    for label, books in stbb.list_all_urdu_books().items():
        p = pick_books(books, args.per_class)
        print(f"  {label:6}: {len(books):2} urdu books -> picked {[b.title for b in p]}")
        picks.extend(p)
    if args.limit:
        picks = picks[: args.limit]
    print(f"probing {len(picks)} books ({args.max_mb} MB each, 2 workers)...\n")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = {ex.submit(probe, b, workdir, args.max_mb): b for b in picks}
        for fut in as_completed(futures):
            b = futures[fut]
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                print(f"  {b.class_label:6} id={b.id:<4} FETCH_FAIL {exc}")
                results.append({"book": b.to_dict(), "verdict": "FETCH_FAIL", "detail": str(exc)[:120]})

    results.sort(key=lambda r: (r["book"]["class_label"], r["book"]["id"]))
    print(f"\n{'Class':6} {'ID':5} {'Verdict':12} {'Year':8} Title | detail")
    for r in results:
        b = r["book"]
        detail = f"fonts={r.get('n_fonts')} urdu_chars={r.get('urdu_chars')} pres_forms={r.get('presentation_forms')}"
        print(f"{b['class_label']:6} {b['id']:<5} {r['verdict']:12} {b['year']:8} {b['title'][:32]:32} | {detail}")

    counts: dict[str, int] = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print(f"\nsummary: {json.dumps(counts)}")
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report written to {out_path}")


if __name__ == "__main__":
    main()
