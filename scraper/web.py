"""Education-domain Urdu web sources: sitemap discovery + article extraction.

Discovery strategy (validated 2026-11-04):
  - BBC Urdu: https://www.bbc.com/urdu/sitemap.xml (recent articles) + index.xml RSS
  - Independent Urdu: https://www.independenturdu.com/sitemap.xml?page=N
    (30K urls/page; article URLs carry Urdu slugs -> keyword filter)
  - Jang: Cloudflare-gated — needs Playwright, later.

Extraction: trafilatura + py3langid (keep only confident Urdu), with
per-document provenance (url, source, discovered_from, fetched_at).
"""

from __future__ import annotations

import http.client
import re
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict, field

import trafilatura
import py3langid

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (urdu-ner-scraper/0.1)"}

#: education-domain keywords matched against URL-decoded slugs and titles
EDU_KEYWORDS = [
    "تعلیم", "تعلیمی", "تدریس", "اسکول", "سکول", "کالج", "یونیورسٹی", "جامعہ",
    "استاد", "اساتذہ", "ٹیچر", "طالب علم", "طالبِ علم", "طلبہ", "طلباء",
    "طالبات", "امتحان", "نتائج", "بورڈ", "نصاب", "درسگاہ", "پرنسپل",
    "لیکچرار", "پروفیسر", "مدرسہ", "مدرسے", "پرچہ", "داخلہ", "رجسٹریشن",
    "ڈگری", "کلاس", "شاگرد", "پڑھائی",
]


@dataclass
class Article:
    url: str
    source: str
    title: str
    text: str
    lang: str
    lang_score: float
    fetched_at: float
    discovered_from: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _get(url: str, timeout: int = 40, retries: int = 3) -> str:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, http.client.IncompleteRead, TimeoutError, OSError) as exc:
            last_exc = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} attempts: {url[:100]} ({last_exc})")


def _locs(xml: str) -> list[str]:
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)


def discover_bbc_urdu(limit: int = 200) -> list[tuple[str, str]]:
    """Recent BBC Urdu (url, title) from the live RSS feed (index.xml).

    Note: /urdu/sitemap.xml is a stale 2014 relic — do not use.
    BBC article URLs are opaque IDs (no Urdu slug), so education filtering
    happens on the RSS title, not the URL.
    """
    xml = _get("https://www.bbc.com/urdu/index.xml")
    items = re.findall(r"<item>(.*?)</item>", xml, re.S)
    out: list[tuple[str, str]] = []
    for it in items:
        m_link = re.search(r"<link>\s*([^<\s]+)\s*</link>", it)
        m_title = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", it, re.S)
        if not m_link:
            continue
        u = m_link.group(1).split("?at_medium=")[0]
        if "/urdu/articles/" not in u:
            continue
        title = (m_title.group(1).strip() if m_title else "")
        out.append((u, title))
    return out[:limit]


def discover_independent_urdu(pages: int = 1, limit: int = 500) -> list[tuple[str, str]]:
    """(url, title='') pairs from Independent Urdu sitemap pages (newest first).

    Titles are not in sitemaps; education filtering happens on the URL slug
    (which carries Urdu keywords) after URL-decoding.
    """
    out: list[tuple[str, str]] = []
    for p in range(1, pages + 1):
        try:
            xml = _get(f"https://www.independenturdu.com/sitemap.xml?page={p}",
                       timeout=120, retries=5)
        except RuntimeError as exc:
            print(f"    sitemap page {p} unavailable, continuing: {str(exc)[:80]}")
            break
        out.extend((u, "") for u in _locs(xml) if "/node/" in u)
        if len(out) >= limit:
            break
    return out[:limit]


def looks_education(url: str, title: str = "") -> bool:
    """Keyword filter on URL-decoded slug + title."""
    try:
        decoded = urllib.parse.unquote(url) + " " + title
    except Exception:  # noqa: BLE001
        return False
    return any(k in decoded for k in EDU_KEYWORDS)


def extract_article(url: str, source: str, title_hint: str = "", discovered_from: str = "") -> Article | None:
    """Fetch + extract one article; None if not clean Urdu prose."""
    try:
        html = _get(url, timeout=45)
    except Exception as exc:  # noqa: BLE001
        print(f"    fetch fail {url[:80]}: {str(exc)[:60]}")
        return None
    text = trafilatura.extract(html, favor_recall=True, include_comments=False) or ""
    md = trafilatura.extract_metadata(html)
    title = (getattr(md, "title", "") or "") or title_hint
    if len(text) < 300:
        return None
    letters = [c for c in text if not c.isspace()]
    arabic_ratio = sum(1 for c in letters if "\u0600" <= c <= "\u06FF") / max(len(letters), 1)
    if arabic_ratio < 0.5:
        return None
    lang, score = py3langid.classify(text[:2000])
    if lang != "ur":
        return None
    return Article(url=url, source=source, title=title, text=text,
                   lang=lang, lang_score=round(score, 2),
                   fetched_at=time.time(), discovered_from=discovered_from)


def crawl_education(source: str, candidates: list[tuple[str, str]], max_articles: int,
                    delay: float = 2.0, seen: set[str] | None = None) -> list[Article]:
    """Filter (url, title_hint) candidates for education keywords, extract up to
    max_articles. Education match uses the URL slug AND the title hint; for
    opaque URLs (BBC) the title does the work."""
    seen = seen if seen is not None else set()
    out: list[Article] = []
    for u, title_hint in candidates:
        if len(out) >= max_articles:
            break
        if u in seen or not looks_education(u, title_hint):
            continue
        seen.add(u)
        art = extract_article(u, source, title_hint=title_hint, discovered_from="discovery")
        if art:
            out.append(art)
            print(f"    [{len(out)}/{max_articles}] {art.title[:60]} ({len(art.text)} chars)")
        time.sleep(delay)
    return out
