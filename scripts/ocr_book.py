#!/usr/bin/env python3
"""OCR a textbook PDF with free-tier Gemini (resumable, per-page cache).

Examples:
    uv run python scripts/ocr_book.py --pdf data/pdfs/stbb/18.pdf --book-id 18 --source stbb
    uv run python scripts/ocr_book.py --pdf data/pdfs/stbb/18.pdf --book-id 18 --first-page 4 --last-page 20
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from textbooks import ocr  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", required=True, help="path to the book PDF")
    ap.add_argument("--book-id", required=True, help="id for the cache dir (e.g., STBB book id)")
    ap.add_argument("--source", default="stbb", help="source label for provenance")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--first-page", type=int, default=0)
    ap.add_argument("--last-page", type=int, default=None)
    ap.add_argument("--pace", type=float, default=2.5, help="seconds between requests")
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.exists():
        raise SystemExit(f"PDF not found: {pdf}")
    out_dir = Path("data/ocr") / args.source / args.book_id

    t0 = time.time()
    print(f"OCR {pdf} -> {out_dir} (dpi={args.dpi}, pages {args.first_page}..{args.last_page})")
    stats = ocr.ocr_book(
        str(pdf), str(out_dir), dpi=args.dpi, first_page=args.first_page,
        last_page=args.last_page, pace_seconds=args.pace,
    )
    print(f"done in {time.time() - t0:.0f}s: {stats}")
    print(f"consolidated text: {out_dir / 'book.txt'}")


if __name__ == "__main__":
    main()
