"""Text-layer validation for Urdu PDFs.

Decision #5 in PLAN.md: use only PDFs with a real, copyable text layer —
no OCR in v1. This module detects, per PDF, whether usable Urdu text can
be extracted, using pymupdf.

Verdicts:
  TEXT_OK       enough Urdu text, no Arabic Presentation Forms artifacts
  TEXT_BROKEN   text layer exists but is mangled (ligature-order damage)
  IMAGE_ONLY    no usable text (scanned / exported as images)
  UNREADABLE    file could not be opened as a PDF at all
"""

from __future__ import annotations

import re
from dataclasses import dataclass

try:
    import pymupdf
except ImportError:  # pragma: no cover - pymupdf is a hard project dependency
    pymupdf = None

_ARABIC_BLOCK = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
# Arabic Presentation Forms: the classic signature of broken Nastaliq extraction
_PRESENTATION_FORMS = re.compile(r"[\uFB50-\uFDFF\uFE70-\uFEFF]")
# minimum Urdu characters across the checked pages to count as a real text layer
_MIN_URDU_CHARS = 500


@dataclass
class ProbeResult:
    verdict: str
    n_fonts: int
    urdu_chars: int
    presentation_forms: int
    total_chars: int
    pages_checked: int
    detail: str = ""

    def to_dict(self) -> dict:
        return vars(self)


def probe_pdf(path: str, pages: int = 6) -> ProbeResult:
    """Check the first *pages* pages of a PDF for a usable Urdu text layer."""
    if pymupdf is None:  # pragma: no cover
        raise RuntimeError("pymupdf is not installed — run `uv sync`")
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001 — pymupdf raises varied types
        return ProbeResult("UNREADABLE", 0, 0, 0, 0, 0, str(exc)[:120])

    try:
        n = min(pages, doc.page_count)
        n_fonts = sum(len(doc.get_page_fonts(i)) for i in range(n))
        text = "".join(doc[i].get_text() for i in range(n))
    finally:
        doc.close()

    urdu_chars = len(_ARABIC_BLOCK.findall(text))
    pres_forms = len(_PRESENTATION_FORMS.findall(text))
    total = len(text)

    if urdu_chars > _MIN_URDU_CHARS:
        verdict = "TEXT_OK" if pres_forms == 0 else "TEXT_BROKEN"
    else:
        verdict = "IMAGE_ONLY"

    return ProbeResult(
        verdict=verdict,
        n_fonts=n_fonts,
        urdu_chars=urdu_chars,
        presentation_forms=pres_forms,
        total_chars=total,
        pages_checked=n,
    )
