"""Client for the Sindh Textbook Board (STBB) eBooks portal.

Portal: https://portal.stbb.edu.pk/ebooks
  - class listing : class.php?id=<CLASS_ID>&medium=Urdu
  - book page     : book.php?id=<BOOK_ID>   (metadata, cover, PDF link)
  - PDF download  : pdf_proxy.php?id=<BOOK_ID>&download=1  (~10 MB, ~40 KB/s,
                    supports HTTP Range → resumable)

Verified 2026-11: portal is live and scrapeable; server is slow, so keep
concurrency low (1-2 streams) and always resume partial downloads.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict

BASE = "https://portal.stbb.edu.pk/ebooks"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (urdu-ner-scraper/0.1)"

#: class.php ids for every class, Kachi (ECCE) through Class XII
CLASS_IDS: dict[int, str] = {
    17: "Kachi",
    1: "I",
    3: "II",
    4: "III",
    5: "IV",
    6: "V",
    7: "VI",
    8: "VII",
    9: "VIII",
    10: "IX",
    11: "X",
    14: "XI",
    15: "XII",
}

_DOWNLOAD_URL = BASE + "/pdf_proxy.php?id={bid}&download=1"


@dataclass
class Book:
    id: int
    title: str
    year: str
    class_label: str
    medium: str = "Urdu"

    def to_dict(self) -> dict:
        return asdict(self)


def _http_get(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _parse_books(class_page_html: str, class_label: str) -> list[Book]:
    """Extract books from a class listing page.

    Card layout: <a href=".../book.php?id=N" class="book-card">
                     <img ... alt="Clean Title">
                     ... 📖 Urdu <title> <CLASS> • 2024-25 ...
                 </a>
    The img alt text carries a clean title; inner text is the fallback.
    """
    books: list[Book] = []
    seen: set[int] = set()
    anchor_re = re.compile(
        r'<a href="(?:https?://portal\.stbb\.edu\.pk/ebooks/)?book\.php\?id=(\d+)"[^>]*>(.*?)</a>',
        re.S,
    )
    alt_re = re.compile(r'alt="([^"]+)"')
    for m in anchor_re.finditer(class_page_html):
        bid = int(m.group(1))
        if bid in seen:
            continue
        seen.add(bid)
        inner = m.group(2)
        alt = alt_re.search(inner)
        if alt:
            title = alt.group(1).strip()
        else:
            # strip tags, emoji, medium badge, trailing "• year"
            txt = re.sub(r"<[^>]+>", " ", inner)
            txt = re.sub(r"^[^\w]*(?:Urdu|Sindhi|English)?\s*", "", txt.strip())
            title = txt.split("•")[0].strip()
            # collapse a trailing repeated class label, e.g. "Urdu Reader IV IV"
            if class_label:
                title = re.sub(
                    rf"\s+{re.escape(class_label)}\s+{re.escape(class_label)}$",
                    f" {class_label}",
                    title,
                )
        year_m = re.search(r"•\s*(20\d{2}(?:-\d{2})?)", inner)
        year = year_m.group(1) if year_m else "?"
        books.append(Book(id=bid, title=title, year=year, class_label=class_label))
    return books


def list_urdu_books(class_id: int, retries: int = 3) -> list[Book]:
    """List Urdu-medium books for one class id (see CLASS_IDS)."""
    label = CLASS_IDS[class_id]
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            html = _http_get(f"{BASE}/class.php?id={class_id}&medium=Urdu")
            return _parse_books(html, label)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed to list class {class_id} ({label}): {last_exc}")


def list_all_urdu_books() -> dict[str, list[Book]]:
    """List Urdu-medium books for every class, keyed by class label."""
    return {CLASS_IDS[cid]: list_urdu_books(cid) for cid in CLASS_IDS}


def download_pdf(
    book_id: int,
    dest: str,
    *,
    resume: bool = True,
    max_bytes: int | None = None,
    chunk_size: int = 256 * 1024,
    read_timeout: int = 90,
    max_retries: int = 5,
    progress: bool = False,
) -> int:
    """Download a book PDF to *dest*, resuming a partial file if present.

    max_bytes: stop after this many bytes (probing partial PDFs).
    Returns the number of bytes written in total (file size on disk).
    The STBB server is slow (~40 KB/s) — do not raise concurrency above ~2.
    """
    import os

    start = 0
    mode = "wb"
    if resume and os.path.exists(dest):
        start = os.path.getsize(dest)
        mode = "ab"

    headers = {"User-Agent": UA}
    if start or max_bytes:
        range_end = f"-{start + max_bytes - 1}" if max_bytes else "-"
        headers["Range"] = f"bytes={start}{range_end}"

    attempt = 0
    while True:
        try:
            req = urllib.request.Request(_DOWNLOAD_URL.format(bid=book_id), headers=headers)
            resp = urllib.request.urlopen(req, timeout=read_timeout)
            break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            attempt += 1
            if attempt > max_retries:
                raise RuntimeError(f"download failed for book {book_id}: {exc}") from exc
            time.sleep(3 * attempt)

    status = getattr(resp, "status", 200)
    if status == 200 and start > 0:
        # server ignored the Range header — restart from scratch
        start, mode = 0, "wb"
    with open(dest, mode) as f:
        written_here = 0
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)
            written_here += len(chunk)
            if max_bytes and written_here >= max_bytes:
                break
            if progress and written_here % (1024 * 1024) < chunk_size:
                print(f"  book {book_id}: {os.path.getsize(dest) / 1e6:.1f} MB", flush=True)
    resp.close()
    return os.path.getsize(dest)
