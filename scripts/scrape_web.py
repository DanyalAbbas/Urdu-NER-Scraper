#!/usr/bin/env python3
"""Scrape education-domain Urdu articles into provenance-tracked JSONL.

Examples:
    uv run python scripts/scrape_web.py --source bbc_urdu --max 20
    uv run python scripts/scrape_web.py --source independent_urdu --pages 1 --max 20
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scraper import web  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, choices=["bbc_urdu", "independent_urdu"])
    ap.add_argument("--max", type=int, default=20, help="max articles to extract")
    ap.add_argument("--pages", type=int, default=1, help="sitemap pages to scan (independent_urdu)")
    ap.add_argument("--out", default=None, help="output jsonl (default data/web/<source>.jsonl)")
    args = ap.parse_args()

    out_path = Path(args.out or f"data/web/{args.source}.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"discovering candidates for {args.source}...")
    if args.source == "bbc_urdu":
        candidates = web.discover_bbc_urdu(limit=800)
    else:
        candidates = web.discover_independent_urdu(pages=args.pages, limit=60000)
    print(f"  {len(candidates)} candidate urls")

    articles = web.crawl_education(args.source, candidates, max_articles=args.max)

    mode = "a" if out_path.exists() else "w"
    with out_path.open(mode, encoding="utf-8") as f:
        for a in articles:
            f.write(json.dumps(a.to_dict(), ensure_ascii=False) + "\n")
    print(f"wrote {len(articles)} articles -> {out_path} ({mode} mode)")


if __name__ == "__main__":
    main()
