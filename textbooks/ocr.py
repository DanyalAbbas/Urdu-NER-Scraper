"""Textbook OCR via free-tier Gemini.

Validated 2026-11-04 on a real STBB Urdu Reader IV page: near-perfect
Nastaliq transcription incl. diacritics. Free tier ≈ 1,500 requests/day
≈ 7–10 books/day. Per-page JSONL cache makes every run resumable —
a page that succeeded is never OCR'd twice.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

import pymupdf

from annotate import llm

OCR_PROMPT = (
    "This is a page from an Urdu textbook (Nastaliq script). Transcribe ALL Urdu "
    "text on this page exactly as written, line by line, in reading order. Skip "
    "page furniture only (page numbers, running headers/footers, cover art). Do "
    "not translate. Do not add commentary. Output only the Urdu text."
)


def render_pages(pdf_path: str, dpi: int = 150, first_page: int = 0, last_page: int | None = None):
    """Yield (page_no, jpeg_bytes) for the given page range (0-based)."""
    doc = pymupdf.open(pdf_path)
    n = doc.page_count if last_page is None else min(last_page, doc.page_count)
    for i in range(first_page, n):
        pix = doc[i].get_pixmap(dpi=dpi)
        yield i, pix.tobytes("jpeg")
    doc.close()


def ocr_page(jpeg_bytes: bytes, env: dict[str, str] | None = None) -> tuple[str, str]:
    """OCR one page image. Returns (text, model_used)."""
    return llm.gemini_generate(llm.gemini_image_prompt(OCR_PROMPT, jpeg_bytes), env=env)


def ocr_book(
    pdf_path: str,
    out_dir: str,
    *,
    dpi: int = 150,
    first_page: int = 0,
    last_page: int | None = None,
    pace_seconds: float = 2.5,
    resume: bool = True,
    progress_every: int = 10,
) -> dict:
    """OCR a whole book into out_dir/pages.jsonl (one line per page), resumable."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cache_file = out / "pages.jsonl"

    done: dict[int, dict] = {}
    if resume and cache_file.exists():
        for line in cache_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec.get("status") == "ok":
                    done[rec["page_no"]] = rec

    env = llm.load_env()
    stats = {"ok": 0, "empty": 0, "fail": 0, "skipped_cached": 0}
    cache = cache_file.open("a", encoding="utf-8")

    def emit(rec: dict) -> None:
        cache.write(json.dumps(rec, ensure_ascii=False) + "\n")
        cache.flush()

    total = None
    try:
        import pymupdf as _pm
        total = _pm.open(pdf_path).page_count
    except Exception:  # noqa: BLE001
        pass

    for page_no, jpeg in render_pages(pdf_path, dpi=dpi, first_page=first_page, last_page=last_page):
        if page_no in done:
            stats["skipped_cached"] += 1
            continue
        rec = {"page_no": page_no, "status": "fail", "text": "", "model": "", "ts": time.time()}
        try:
            text, model = ocr_page(jpeg, env=env)
            text = text.strip()
            rec.update(text=text, model=model, status="ok" if text else "empty")
            stats["ok" if text else "empty"] += 1
        except Exception as exc:  # noqa: BLE001 — log and continue; rerun resumes
            rec["detail"] = str(exc)[:200]
            stats["fail"] += 1
        emit(rec)
        if (page_no + 1) % progress_every == 0:
            print(f"  page {page_no + 1}/{total}: {json.dumps(stats)}", flush=True)
        time.sleep(pace_seconds + random.uniform(0, 1.0))
    cache.close()

    # consolidated plain-text file for downstream cleaning
    pages = {}
    if cache_file.exists():
        for line in cache_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec.get("status") == "ok":
                    pages[rec["page_no"]] = rec["text"]
    (out / "book.txt").write_text("\n\n".join(pages[k] for k in sorted(pages)), encoding="utf-8")
    return stats
