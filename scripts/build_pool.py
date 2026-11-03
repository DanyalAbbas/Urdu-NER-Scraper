#!/usr/bin/env python3
"""Build the filtered, deduplicated sentence pool from scraped/OCR sources.

Sources (whatever exists):
    data/web/*.jsonl            scraped articles (scraper/web.py output)
    data/ocr/<source>/<id>/book.txt   OCR'd textbook text

Output: data/pool/pool.jsonl (+ stats to stdout)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clean.normalize import build_pool  # noqa: E402


def load_documents(data_dir: Path) -> list[dict]:
    docs: list[dict] = []
    for jsonl in sorted((data_dir / "web").glob("*.jsonl")):
        source = jsonl.stem
        for i, line in enumerate(jsonl.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            a = json.loads(line)
            docs.append({
                "doc_id": f"web_{source}_{i}",
                "source": source,
                "text": a["text"],
                "provenance": a["url"],
            })
    for book in sorted((data_dir / "ocr").glob("*/*/book.txt")):
        source, book_id = book.parts[-3], book.parts[-2]
        docs.append({
            "doc_id": f"ocr_{source}_{book_id}",
            "source": f"ocr_{source}",
            "text": book.read_text(encoding="utf-8"),
            "provenance": f"ocr:{source}:book:{book_id}",
        })
    return docs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out", default="data/pool/pool.jsonl")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    docs = load_documents(data_dir)
    print(f"{len(docs)} documents loaded "
          f"({sum(1 for d in docs if d['source'].startswith('ocr'))} OCR books, "
          f"{sum(1 for d in docs if not d['source'].startswith('ocr'))} web articles)")

    sentences, stats = build_pool(docs)
    print("stats:", json.dumps(stats, ensure_ascii=False))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for s in sentences:
            f.write(json.dumps(s.to_dict(), ensure_ascii=False) + "\n")
    by_source: dict[str, int] = {}
    for s in sentences:
        by_source[s.source] = by_source.get(s.source, 0) + 1
    print(f"{len(sentences)} sentences -> {out}")
    print("by source:", json.dumps(by_source, ensure_ascii=False))


if __name__ == "__main__":
    main()
