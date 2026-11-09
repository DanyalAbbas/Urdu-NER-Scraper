"""Synthetic PHONE / CNIC injection (PLAN.md §5b).

Phone numbers and CNICs are never scraped. We generate fictitious numbers in
valid Pakistani formats and append a natural carrier clause to a pool
sentence. The generator returns the exact character offsets, so the resulting
spans are gold by construction.

All numbers are fictitious: area/network codes are public, but subscriber
digits are random. The seed makes every run reproducible.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, asdict

#: public Pakistani mobile network prefixes (03XX)
MOBILE_PREFIXES = ["300", "301", "302", "303", "304", "305", "306", "307", "308",
                   "309", "310", "311", "312", "313", "314", "315", "316", "317",
                   "318", "319", "320", "321", "322", "323", "324", "325", "326",
                   "327", "328", "329", "330", "331", "332", "333", "334", "335",
                   "336", "337", "338", "339", "340", "341", "342", "343", "344",
                   "345", "346", "347", "348", "349"]

#: public landline area codes (0XX)
LANDLINE_CODES = ["21", "22", "41", "42", "51", "52", "53", "54", "55", "56",
                  "57", "58", "61", "62", "63", "64", "65", "66", "67", "68",
                  "71", "72", "73", "74", "81", "82", "83", "84", "85", "86",
                  "91", "92", "93", "94", "95", "96", "97", "98", "99"]

#: public CNIC district/region codes (first 5 digits)
CNIC_REGIONS = ["42101", "42201", "42301", "42401", "42501", "42601",
                "35201", "35202", "35301", "35401", "35501", "35601",
                "61101", "61001", "61201", "61301", "61401", "61501",
                "17201", "17301", "17401", "13101", "13201", "13301"]

PHONE_TEMPLATES = [
    "مزید معلومات کے لیے {x} پر رابطہ کریں۔",
    "رابطے کے لیے فون نمبر {x} استعمال کریں۔",
    "کسی بھی سوال کے لیے {x} پر کال کریں۔",
    "داخلے کی معلومات کے لیے {x} پر رابطہ کریں۔",
    "معلومات کے لیے {x} پر پیغام بھیجیں۔",
    "ہیلپ لائن {x} پر دستیاب ہے۔",
]

CNIC_TEMPLATES = [
    "درخواست کے ساتھ شناختی کارڈ نمبر {x} لازمی جمع کروائیں۔",
    "شناختی کارڈ نمبر {x} کی نقل منسلک کریں۔",
    "فارم میں شناختی کارڈ نمبر {x} درج کریں۔",
    "رجسٹریشن کے لیے شناختی کارڈ نمبر {x} درکار ہے۔",
    "امتحان فارم پر شناختی کارڈ نمبر {x} لکھیں۔",
]

#: sentence terminators used by the cleaner
_TERMINATORS = ("۔", "؟", "!", ".")


@dataclass
class GoldSpan:
    start: int
    end: int
    tag: str
    text: str
    synthetic: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def gen_phone(rng: random.Random) -> str:
    """Random Pakistani phone number in one of the common written formats."""
    form = rng.choices(
        ["mobile_dash", "mobile_plus92", "landline_dash", "mobile_plain"],
        weights=[55, 20, 15, 10],
    )[0]
    if form == "landline_dash":
        return f"0{rng.choice(LANDLINE_CODES)}-{rng.randint(1_000_000, 9_999_999)}"
    mobile = f"{rng.choice(MOBILE_PREFIXES)}{rng.randint(1_000_000, 9_999_999)}"
    if form == "mobile_plus92":
        return f"+92-{mobile}"
    if form == "mobile_plain":
        return f"0{mobile}"
    return f"0{mobile[:3]}-{mobile[3:]}"


def gen_cnic(rng: random.Random) -> str:
    """Fictitious 13-digit CNIC: 5-7-1 with a real district code prefix."""
    region = rng.choice(CNIC_REGIONS)
    serial = rng.randint(1_000_000, 9_999_999)
    check = rng.randint(1, 9)
    return f"{region}-{serial}-{check}"


def _ensure_terminated(sentence: str) -> str:
    return sentence if sentence.rstrip().endswith(_TERMINATORS) else sentence.rstrip() + "۔"


def inject(
    sentence: str,
    rng: random.Random,
    *,
    phone_prob: float = 0.08,
    cnic_prob: float = 0.08,
    max_entities: int = 2,
) -> tuple[str, list[GoldSpan]]:
    """Append carrier clauses to one sentence. Returns (new_text, gold_spans).

    phone_prob / cnic_prob are independent per-sentence probabilities. The
    returned spans are exact character offsets into new_text.
    """
    text = _ensure_terminated(sentence)
    spans: list[GoldSpan] = []

    kinds: list[str] = []
    if rng.random() < phone_prob:
        kinds.append("PHONE")
    if rng.random() < cnic_prob:
        kinds.append("CNIC")
    rng.shuffle(kinds)
    kinds = kinds[:max_entities]
    if not kinds:
        return text, spans

    for kind in kinds:
        if kind == "PHONE":
            value = gen_phone(rng)
            clause = rng.choice(PHONE_TEMPLATES).format(x=value)
        else:
            value = gen_cnic(rng)
            clause = rng.choice(CNIC_TEMPLATES).format(x=value)

        base_len = len(text)
        text = f"{text} {clause}"
        local = clause.index(value)
        start = base_len + 1 + local
        spans.append(GoldSpan(start=start, end=start + len(value), tag=kind, text=value))

    return text, spans
