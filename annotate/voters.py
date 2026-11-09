"""3-voter majority annotation with BIO conversion and synthetic-gold forcing.

Flow per sentence:
  1. three free-tier voters annotate independently (Groq / Gemini / OpenRouter)
  2. entity text is resolved to token spans locally (models are unreliable at
     Urdu character offsets)
  3. keep a span if >= 2 voters agree on the exact token span + tag
  4. synthetic PHONE/CNIC gold spans are forced in; voter spans overlapping
     them are discarded
  5. emit token-level BIO + character spans (for Label Studio) + agreement
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from annotate import llm, prompts
from annotate.schema import TAGS

# ---------------------------------------------------------------------------
# tokenization + span alignment

_WORD_CHAR = r"\w\u200c\u200d"
_EDGE_PUNCT = " \t\n\r۔،؛؟!.,:;\"'()[]{}<>«»"
_TOKEN_RE = re.compile(r"[^\s۔،؛؟!()\[\]{}«»\"“”‘’]+|[۔،؛؟!()\[\]{}«»\"“”‘’]")


@dataclass(frozen=True)
class Token:
    text: str
    start: int
    end: int


def tokenize(text: str) -> list[Token]:
    """Whitespace tokens with Urdu sentence punctuation split off.

    "آمنہ،" -> ["آمنہ", "،"] so a PER span never swallows the comma.
    Hyphens are kept (phone numbers, CNICs).
    """
    return [Token(m.group(), m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]


def _align(tokens: list[Token], start: int, end: int) -> tuple[int, int, bool] | None:
    """Character span -> (i0, i1, misaligned). None if it covers no token."""
    i0 = next((i for i, t in enumerate(tokens) if t.end > start), None)
    i1 = next((i for i in range(len(tokens) - 1, -1, -1) if tokens[i].start < end), None)
    if i0 is None or i1 is None or i1 < i0:
        return None
    misaligned = tokens[i0].start != start or tokens[i1].end != end
    return i0, i1 + 1, misaligned


def find_occurrences(text: str, needle: str) -> list[tuple[int, int]]:
    """All whole-word occurrences of needle in text (Urdu-aware boundaries)."""
    if not needle:
        return []
    pattern = rf"(?<![{_WORD_CHAR}]){re.escape(needle)}(?![{_WORD_CHAR}])"
    return [(m.start(), m.end()) for m in re.finditer(pattern, text)]


def clean_entity_text(text: str) -> str:
    return text.strip().strip(_EDGE_PUNCT).strip()


# ---------------------------------------------------------------------------
# model output parsing

_TAG_ALIASES = {
    "PERSON": "PER", "PEOPLE": "PER", "NAME": "PER",
    "LOCATION": "LOC", "GPE": "LOC", "CITY": "LOC", "COUNTRY": "LOC",
    "FACILITY": "PLACE", "FAC": "PLACE", "LOCATION_PLACE": "PLACE",
    "JOB": "JOBTITLE", "TITLE": "JOBTITLE", "DESIGNATION": "JOBTITLE",
    "ORGANIZATION": "ORG", "ORGANISATION": "ORG", "INSTITUTION": "ORG",
    "DATE": "DAY", "TIME": "TIME", "DAY": "DAY",
    "PHONE_NUMBER": "PHONE", "PHONENUMBER": "PHONE", "MOBILE": "PHONE",
    "CNIC_NUMBER": "CNIC", "NIC": "CNIC", "IDENTITY_NUMBER": "CNIC",
}


def normalize_tag(raw: str) -> str | None:
    tag = str(raw or "").strip().upper()
    if tag.startswith(("B-", "I-", "S-", "E-")):
        tag = tag[2:]
    tag = _TAG_ALIASES.get(tag, tag)
    return tag if tag in TAGS else None


def _extract_json(raw: str) -> object | None:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            return None
    try:
        return json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None


def parse_annotation(raw: str, batch_ids: list[str]) -> dict[str, list[dict]]:
    """Parse one voter's output into {sentence_id: [{text, tag}]}."""
    data = _extract_json(raw)
    out: dict[str, list[dict]] = defaultdict(list)
    if data is None:
        return out
    items = data if isinstance(data, list) else data.get("sentences") or data.get("results") or []
    if not isinstance(items, list):
        return out
    for pos, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        sid = item.get("sentence_id", item.get("id", batch_ids[pos] if pos < len(batch_ids) else None))
        sid = str(sid)
        if sid not in batch_ids and pos < len(batch_ids):
            sid = batch_ids[pos]
        entities = item.get("entities") or item.get("tags") or item.get("spans") or []
        if not isinstance(entities, list):
            continue
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            text = ent.get("text") or ent.get("entity") or ent.get("span") or ent.get("mention")
            tag = normalize_tag(ent.get("tag") or ent.get("label") or ent.get("type") or "")
            if text and tag:
                out[sid].append({"text": str(text), "tag": tag})
    return out


# ---------------------------------------------------------------------------
# voters

def _gemini(prompt: str) -> tuple[str, str]:
    return llm.gemini_generate([{"text": prompt}], temperature=0.0, max_output_tokens=6000)


def _groq(prompt: str) -> tuple[str, str]:
    return llm.groq_chat([{"role": "user", "content": prompt}], max_tokens=4000), llm.GROQ_DEFAULT


def _groq2(prompt: str) -> tuple[str, str]:
    """Second Groq model — used as the third voter because OpenRouter's free
    tier is currently rate-limited/unreliable (validated 2026-11-04)."""
    return llm.groq_chat([{"role": "user", "content": prompt}],
                         model="openai/gpt-oss-20b", max_tokens=4000), "openai/gpt-oss-20b"


def _openrouter(prompt: str) -> tuple[str, str]:
    return llm.openrouter_chat([{"role": "user", "content": prompt}], max_tokens=8000), llm.OPENROUTER_DEFAULT


VOTER_FUNCS = {"groq": _groq, "gemini": _gemini, "groq2": _groq2, "openrouter": _openrouter}

#: default stack: fast and reliable. OpenRouter remains available via --voters
#: but its free lineup rotates and is frequently 429/truncated.
DEFAULT_VOTERS = ["groq", "gemini", "groq2"]


def _call_voter(name: str, prompt: str, retries: int = 2) -> tuple[str, str]:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return VOTER_FUNCS[name](prompt)
        except Exception as exc:  # noqa: BLE001 — provider hiccups are expected
            last = exc
            if attempt < retries:
                # Groq free tier is 8K tokens/minute: a 429 needs a real wait,
                # not a 3-second retry.
                delay = 20 * (attempt + 1) if "429" in str(exc) else 3 * (attempt + 1)
                time.sleep(delay)
    raise RuntimeError(f"{name} failed: {str(last)[:160]}")


def run_voters(texts: list[str], voter_names: list[str] | None = None,
               system_prompt: str | None = None) -> dict:
    """Annotate one batch. Returns {'by_voter': {name: {sid: ents}}, 'models': {...}, 'errors': {...}}."""
    voter_names = voter_names or list(DEFAULT_VOTERS)
    batch = [(str(i + 1), t) for i, t in enumerate(texts)]
    ids = [sid for sid, _ in batch]
    prompt = prompts.build_batch_prompt(batch, system=system_prompt)

    results: dict[str, str] = {}
    models: dict[str, str] = {}
    errors: dict[str, str] = {}

    def work(name: str) -> None:
        try:
            raw, model = _call_voter(name, prompt)
            if _extract_json(raw) is None:
                raise RuntimeError(f"unparseable output: {raw[:120]}")
            results[name] = raw
            models[name] = model
        except Exception as exc:  # noqa: BLE001
            errors[name] = str(exc)[:200]

    with ThreadPoolExecutor(max_workers=len(voter_names)) as pool:
        list(pool.map(work, voter_names))

    return {
        "by_voter": {name: parse_annotation(raw, ids) for name, raw in results.items()},
        "models": models,
        "errors": errors,
        "raw": results,
    }


# ---------------------------------------------------------------------------
# voting + BIO

@dataclass
class Span:
    i0: int
    i1: int
    tag: str
    votes: int = 0
    synthetic: bool = False
    text: str = ""

    def key(self) -> tuple[int, int, str]:
        return self.i0, self.i1, self.tag


@dataclass
class SentenceAnnotation:
    tokens: list[str]
    bio: list[list[str]]
    spans: list[dict]
    agreement: str
    n_voters: int
    conflicts: list[dict] = field(default_factory=list)
    synthetic_detected: dict[str, str] = field(default_factory=dict)
    singletons: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "tokens": self.tokens,
            "bio": self.bio,
            "spans": self.spans,
            "agreement": self.agreement,
            "n_voters": self.n_voters,
            "conflicts": self.conflicts,
            "synthetic_detected": self.synthetic_detected,
            "singletons": self.singletons,
        }


def _voter_token_spans(text: str, entities: list[dict]) -> set[tuple[int, int, str]]:
    tokens = tokenize(text)
    spans: set[tuple[int, int, str]] = set()
    for ent in entities:
        surface = clean_entity_text(ent["text"])
        tag = ent["tag"]
        if not surface:
            continue
        for start, end in find_occurrences(text, surface):
            aligned = _align(tokens, start, end)
            if aligned:
                i0, i1, _ = aligned
                spans.add((i0, i1, tag))
    return spans


def vote_sentence(text: str, by_voter: dict[str, dict], sentence_id: str,
                  gold_spans: list[dict] | None = None) -> SentenceAnnotation:
    tokens = tokenize(text)
    gold_spans = gold_spans or []
    n_voters = len(by_voter)

    voter_spans = {
        name: _voter_token_spans(text, ents.get(sentence_id, []))
        for name, ents in by_voter.items()
    }

    votes: dict[tuple[int, int, str], int] = defaultdict(int)
    for spans in voter_spans.values():
        for key in spans:
            votes[key] += 1

    threshold = 2
    accepted: list[Span] = [Span(i0, i1, tag, votes=v) for (i0, i1, tag), v in votes.items() if v >= threshold]
    rejected = [Span(i0, i1, tag, votes=v) for (i0, i1, tag), v in votes.items() if v < threshold]

    # greedy overlap resolution: more votes, then longer span
    accepted.sort(key=lambda s: (-s.votes, -(s.i1 - s.i0)))
    kept: list[Span] = []
    for cand in accepted:
        if any(not (cand.i1 <= k.i0 or cand.i0 >= k.i1) for k in kept):
            rejected.append(cand)
            continue
        kept.append(cand)

    # synthetic gold is authoritative: drop overlapping voter spans, add gold
    gold_token_spans: list[Span] = []
    for g in gold_spans:
        aligned = _align(tokens, g["start"], g["end"])
        if not aligned:
            continue
        i0, i1, _ = aligned
        gold_token_spans.append(Span(i0, i1, g["tag"], votes=0, synthetic=True))
    if gold_token_spans:
        kept = [k for k in kept if not any(
            not (k.i1 <= g.i0 or k.i0 >= g.i1) for g in gold_token_spans)]
        kept.extend(gold_token_spans)

    kept.sort(key=lambda s: s.i0)
    spans: list[dict] = []
    for s in kept:
        start = tokens[s.i0].start
        end = tokens[s.i1 - 1].end
        spans.append({
            "start": start,
            "end": end,
            "tag": s.tag,
            "text": text[start:end],
            "votes": s.votes,
            "synthetic": s.synthetic,
        })

    bio_tags = ["O"] * len(tokens)
    for s in kept:
        bio_tags[s.i0] = f"B-{s.tag}"
        for i in range(s.i0 + 1, s.i1):
            bio_tags[i] = f"I-{s.tag}"

    # agreement on natural entities only; synthetic spans are gold.
    # A "conflict" is a >=2-vote span dropped because it overlapped a stronger
    # one — that is a real disagreement. Single-voter spans are recorded as
    # singletons (useful for prompt iteration) but do not downgrade agreement,
    # otherwise nearly every sentence would need full human review.
    natural = [s for s in kept if not s.synthetic]
    conflict_dicts = [
        {"start": tokens[s.i0].start, "end": tokens[s.i1 - 1].end, "tag": s.tag,
         "text": text[tokens[s.i0].start:tokens[s.i1 - 1].end], "votes": s.votes}
        for s in rejected if s.votes >= 2
    ]
    singleton_dicts = [
        {"start": tokens[s.i0].start, "end": tokens[s.i1 - 1].end, "tag": s.tag,
         "text": text[tokens[s.i0].start:tokens[s.i1 - 1].end]}
        for s in rejected if s.votes == 1
    ]
    if n_voters >= 3:
        agreement = "3/3" if all(s.votes == 3 for s in natural) and not conflict_dicts else "2/3"
    elif n_voters == 2:
        agreement = "2/2" if not conflict_dicts else "1/2"
    else:
        agreement = "insufficient"

    detected: dict[str, str] = {}
    for g in gold_spans:
        aligned = _align(tokens, g["start"], g["end"])
        found = "no"
        if aligned:
            g0, g1, _ = aligned
            for spans_set in voter_spans.values():
                if any(tag == g["tag"] and not (i1 <= g0 or i0 >= g1)
                       for (i0, i1, tag) in spans_set):
                    found = "yes"
                    break
        detected[g["tag"]] = found

    return SentenceAnnotation(
        tokens=[t.text for t in tokens],
        bio=[[tokens[i].text, bio_tags[i]] for i in range(len(tokens))],
        spans=spans,
        agreement=agreement,
        n_voters=n_voters,
        conflicts=conflict_dicts,
        synthetic_detected=detected,
        singletons=singleton_dicts,
    )


# ---------------------------------------------------------------------------
# high-level batch driver

def annotate_items(items: list[dict], *, batch_size: int = 12, pace: float = 2.0,
                   voter_names: list[str] | None = None, progress_every: int = 10,
                   max_failures: int = 3) -> list[dict]:
    """items: [{sentence_id, text, gold_spans?}] -> annotation records.

    Records with fewer than 2 successful voters are marked
    'status': 'incomplete' so the caller can retry them later.
    """
    system_prompt = prompts.build_system_prompt()
    out: list[dict] = []
    consecutive_failures = 0

    for b0 in range(0, len(items), batch_size):
        batch = items[b0:b0 + batch_size]
        texts = [it["text"] for it in batch]
        result = run_voters(texts, voter_names=voter_names, system_prompt=system_prompt)

        if len(result["by_voter"]) < 2:
            consecutive_failures += 1
            for it in batch:
                out.append({**it, "status": "incomplete", "errors": result["errors"]})
            if consecutive_failures >= max_failures:
                print(f"  aborting after {consecutive_failures} consecutive batch failures",
                      flush=True)
                break
        else:
            consecutive_failures = 0
            for i, it in enumerate(batch):
                sid = str(i + 1)
                ann = vote_sentence(it["text"], result["by_voter"], sid,
                                    gold_spans=it.get("gold_spans"))
                out.append({
                    **it,
                    "status": "ok",
                    "models": result["models"],
                    "errors": result["errors"],
                    **ann.to_dict(),
                })

        done = min(b0 + batch_size, len(items))
        if (b0 // batch_size + 1) % progress_every == 0 or done == len(items):
            print(f"  annotated {done}/{len(items)}", flush=True)
        if pace:
            time.sleep(pace)

    return out
