"""Annotation prompt (10-tag schema) with few-shot examples.

Design notes:
  - Models are unreliable at character offsets in Urdu, so we ask for exact
    entity *text* and resolve offsets locally (longest-match, all occurrences).
  - Few-shot examples are normalized (no diacritics) so they look exactly like
    the pool sentences the model will see.
  - Output is a JSON array keyed by sentence number; no prose, no markdown.
  - The prompt is kept compact: Groq's free tier allows only 8K tokens/minute,
    so every example costs throughput. Eight examples cover all ten tags.
"""

from __future__ import annotations

from annotate.schema import TAG_DESCRIPTIONS, TAGS

_EXAMPLES: list[tuple[str, str]] = [
    (
        "استاد احمد رضا نے کلاس میں سبق پڑھایا۔",
        '[{"text": "استاد", "tag": "JOBTITLE"}, {"text": "احمد رضا", "tag": "PER"}]',
    ),
    (
        "کراچی میں واقع جامعہ کراچی کے طلبہ نے پیر کو امتحان دیا۔",
        '[{"text": "کراچی", "tag": "LOC"}, {"text": "جامعہ کراچی", "tag": "ORG"}, '
        '{"text": "پیر", "tag": "DAY"}]',
    ),
    (
        "وزیر تعلیم نے 14 اگست کو یوم آزادی کی تقریب سے خطاب کیا۔",
        '[{"text": "وزیر تعلیم", "tag": "JOBTITLE"}, {"text": "14 اگست", "tag": "DAY"}, '
        '{"text": "یوم آزادی", "tag": "EVENT"}]',
    ),
    (
        "صبح 8 بجے اسکول کی عمارت کے سامنے اجتماع ہوگا۔",
        '[{"text": "صبح 8 بجے", "tag": "TIME"}, {"text": "اسکول کی عمارت", "tag": "PLACE"}]',
    ),
    (
        "مزید معلومات کے لیے 0301-2345678 پر رابطہ کریں۔",
        '[{"text": "0301-2345678", "tag": "PHONE"}]',
    ),
    (
        "درخواست کے ساتھ شناختی کارڈ نمبر 42101-1234567-1 لازمی جمع کروائیں۔",
        '[{"text": "42101-1234567-1", "tag": "CNIC"}]',
    ),
    (
        "اساتذہ نے لائبریری میں اجلاس کیا۔",
        '[{"text": "اساتذہ", "tag": "JOBTITLE"}, {"text": "لائبریری", "tag": "PLACE"}]',
    ),
    (
        "کتابیں میز پر رکھی ہوئی تھیں۔",
        "[]",
    ),
]


def build_system_prompt() -> str:
    tag_lines = "\n".join(f"- {tag}: {TAG_DESCRIPTIONS[tag]}" for tag in TAGS)
    examples = "\n".join(f"{sent} -> {ents}" for sent, ents in _EXAMPLES)
    return f"""You are a careful Urdu named-entity-recognition annotator.

Tags:
{tag_lines}

Rules:
1. Copy entity text exactly; no surrounding punctuation, spaces, or quotes.
2. Role/occupation words are JOBTITLE, with or without a name. A name next to
   a title is a separate PER (استاد احمد رضا -> JOBTITLE "استاد" + PER "احمد رضا").
3. Country/province/district/city/town/village -> LOC. Building, school,
   library, mosque, road, mountain, river -> PLACE (named or generic). Only
   *named* institutions are ORG.
4. Calendar date or weekday -> DAY. Named holiday/occasion -> EVENT. Clock
   times, parts of day, durations, months and years -> TIME.
5. Plain numbers, money, quantities, languages, nationalities -> O.
   Students (طالب علم، طلبہ، شاگرد) are not job titles -> O.
6. Never nest entities. Prefer the longest complete name.

Examples (sentence -> entities):
{examples}

Return ONLY a JSON array, one object per input sentence, in input order:
[{{"sentence_id": <id>, "entities": [{{"text": "<exact substring>", "tag": "<TAG>"}}]}}]
No markdown, no commentary."""


def build_batch_prompt(sentences: list[tuple[str, str]], system: str | None = None) -> str:
    """sentences: [(sentence_id, text)] -> full prompt for one batch."""
    system = system or build_system_prompt()
    numbered = "\n".join(f"{sid}. {text}" for sid, text in sentences)
    return f"{system}\n\nAnnotate these {len(sentences)} sentences:\n{numbered}"
