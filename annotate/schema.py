"""Final label set (10 tags + O) and ZUNERA mapping.

This is the single source of truth for tag names. Everything downstream
(prompts, BIO conversion, Label Studio config, metrics) imports from here.
"""

from __future__ import annotations

#: order matters only for display / config generation
TAGS: list[str] = [
    "PER",
    "LOC",
    "PLACE",
    "JOBTITLE",
    "ORG",
    "TIME",
    "DAY",
    "EVENT",
    "PHONE",
    "CNIC",
]

#: natural-language definitions used verbatim in the annotation prompt
TAG_DESCRIPTIONS: dict[str, str] = {
    "PER": "person name, without titles or honorifics",
    "LOC": "geo-political or administrative location: country, province, division, "
           "district, city, town, village, region",
    "PLACE": "physical place, facility, or geographic feature, named or generic: "
             "building, campus, school, library, mosque, hospital, park, road, "
             "bridge, mountain, river, market",
    "JOBTITLE": "job title, designation, role, or occupation word, with or without "
                "a name (استاد، اساتذہ، پروفیسر، پرنسپل، لیکچرار، ڈاکٹر، کلرک)",
    "ORG": "named organization, institution, body, company, party, or media outlet",
    "TIME": "clock time, part of day, duration, or a month/year used as a time reference",
    "DAY": "calendar-day expression: weekday name or calendar date",
    "EVENT": "named event, occasion, or observance",
    "PHONE": "phone number",
    "CNIC": "national identity card (CNIC) number",
}

#: ZUNERA 23-tag set -> ours. Tags not listed map to O.
ZUNERA_MAP: dict[str, str] = {
    "PER": "PER",
    "LOC": "LOC",
    "GPE": "LOC",
    "PLACE": "PLACE",
    "JOBTITLE": "JOBTITLE",
    "ORG": "ORG",
    "TIME": "TIME",
    "DATE": "DAY",
    "DAY": "DAY",
    "EVENT": "EVENT",
}

#: tags that exist only through synthetic injection (never model-voted)
SYNTHETIC_TAGS: set[str] = {"PHONE", "CNIC"}

BIO_TAGS: list[str] = [f"{prefix}-{tag}" for tag in TAGS for prefix in ("B", "I")] + ["O"]


def map_zunera(tag: str) -> str:
    """Map a ZUNERA tag (without B-/I- prefix) to our schema; unknown -> O."""
    return ZUNERA_MAP.get(tag.upper(), "O")
