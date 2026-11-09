"""Urdu text normalization, sentence splitting, filtering, dedup, pooling.

Implements PLAN.md S4–S5:
  - Unicode NFC + Arabic/Persian variant unification + diacritic stripping
  - sentence split on ۔ ؟ !
  - filters: token count, Urdu-script ratio, Sindhi-letter exclusion,
    poetry blocks
  - MinHash near-duplicate removal (char 5-grams, Jaccard >= 0.8)
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, asdict

from datasketch import MinHash

# ---------------------------------------------------------------------------
# normalization

# Arabic yeh/kaf variants -> Urdu forms; teh marbuta -> gol heh
_CHAR_MAP = {
    "\u064A": "\u06CC",  # ي -> ی
    "\u0649": "\u06CC",  # ى -> ی
    "\u0643": "\u06A9",  # ك -> ک
    "\u0629": "\u06C1",  # ة -> ہ
    "\u0640": "",        # tatweel ـ
}
# harakat / diacritics (اعراب) to strip: 064B–065F, 0670, 06D6–06ED
_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED]")
_WS = re.compile(r"\s+")
#: typographic/ASCII quotes often survive article extraction as stray tokens
_QUOTES = "‘’“”\"'«»"


def normalize_urdu(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    for src, dst in _CHAR_MAP.items():
        text = text.replace(src, dst)
    text = _DIACRITICS.sub("", text)
    text = _WS.sub(" ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# sentence splitting

_SENT_END = re.compile(r"([۔؟!]+)\s*")
# don't treat a full stop as a boundary when between Eastern digits (1۔5) or
# between a digit and a letter (abbreviations) — simple post-check


def split_sentences(text: str) -> list[str]:
    """Split normalized text on ۔ ؟ ! keeping the terminator attached."""
    text = text.replace("\n", " ")
    parts = _SENT_END.split(text)
    sentences: list[str] = []
    buf = ""
    for i, part in enumerate(parts):
        if i % 2 == 0:  # text chunk
            buf += part
        else:  # terminator group
            # skip decimal/ordinal patterns like "۱۔۵"
            if buf and buf[-1].isdigit() and len(part) == 1 and parts[i + 1:i + 2] and \
                    parts[i + 1][:1].isdigit():
                buf += part
                continue
            s = (buf + part).strip().strip(_QUOTES).strip()
            if s:
                sentences.append(s)
            buf = ""
    tail = buf.strip().strip(_QUOTES).strip()
    if tail:
        sentences.append(tail)
    return sentences


# ---------------------------------------------------------------------------
# filters

#: Sindhi-only letters (Urdu corpus must not contain these)
_SINDHI = set("ٽٺٿڀڃڇڌڊڏڍڙڪڳڱڻ")
_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
_DIGIT = re.compile(r"[0-9\u06F0-\u06F9]")


def sentence_ok(sent: str, min_tokens: int = 5, max_tokens: int = 40,
                min_ratio: float = 0.90) -> tuple[bool, str]:
    """Return (ok, reason). reason '' when ok."""
    tokens = sent.split()
    if not (min_tokens <= len(tokens) <= max_tokens):
        return False, "length"
    letters = [c for c in sent if c.isalpha()]
    if not letters:
        return False, "no_letters"
    ratio = sum(1 for c in letters if _ARABIC.match(c)) / len(letters)
    if ratio < min_ratio:
        return False, "script_ratio"
    if any(c in _SINDHI for c in sent):
        return False, "sindhi_letters"
    digit_frac = len(_DIGIT.findall(sent)) / max(len(sent), 1)
    if digit_frac > 0.3:
        return False, "mostly_numbers"
    return True, ""


def is_poem_block(block: str, max_line_tokens: int = 8, min_short_lines: int = 4) -> bool:
    """Heuristic: a block of >=4 consecutive very short lines is verse."""
    lines = [ln for ln in block.splitlines() if ln.strip()]
    if len(lines) < min_short_lines:
        return False
    short = sum(1 for ln in lines if len(ln.split()) <= max_line_tokens)
    return short >= min_short_lines


# ---------------------------------------------------------------------------
# dedup

def _minhash_sig(text: str, num_perm: int = 64) -> MinHash:
    m = MinHash(num_perm=num_perm)
    grams = [text[i:i + 5] for i in range(max(len(text) - 4, 1))]
    for g in grams:
        m.update(g.encode("utf-8"))
    return m


class Deduper:
    """MinHash-based near-duplicate filter (Jaccard >= threshold)."""

    def __init__(self, threshold: float = 0.8, num_perm: int = 64):
        self.threshold = threshold
        self.num_perm = num_perm
        # LSH index for scalable lookup
        from datasketch import MinHashLSH
        self.lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
        self.count = 0

    def add_and_check(self, text: str) -> bool:
        """Add text; return False if it duplicates something already seen."""
        key = hashlib.sha1(text.encode("utf-8")).hexdigest()
        m = _minhash_sig(text, self.num_perm)
        if self.lsh.query(m):
            return False
        self.lsh.insert(key, m)
        self.count += 1
        return True


# ---------------------------------------------------------------------------
# pool building

@dataclass
class PoolSentence:
    sentence_id: str
    doc_id: str
    source: str
    text: str
    tokens: int
    provenance: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def build_pool(documents: list[dict], dedupe: bool = True) -> tuple[list[PoolSentence], dict]:
    """documents: [{doc_id, source, text, provenance?}] -> (sentences, stats).

    Blocks (paragraphs split on blank lines) are poetry-screened before
    sentence splitting.
    """
    deduper = Deduper() if dedupe else None
    out: list[PoolSentence] = []
    stats = {"docs": 0, "blocks": 0, "poem_blocks": 0, "sentences": 0,
             "kept": 0, "deduped": 0, "rejected": 0}

    for doc in documents:
        stats["docs"] += 1
        text = normalize_urdu(doc["text"])
        for block in re.split(r"\n\s*\n", doc["text"]):  # poetry screen on raw block
            stats["blocks"] += 1
            if is_poem_block(block):
                stats["poem_blocks"] += 1
                continue
            for sent in split_sentences(normalize_urdu(block)):
                stats["sentences"] += 1
                ok, reason = sentence_ok(sent)
                if not ok:
                    stats["rejected"] += 1
                    continue
                if deduper is not None and not deduper.add_and_check(sent):
                    stats["deduped"] += 1
                    continue
                sid = hashlib.sha1(f"{doc['doc_id']}|{sent}".encode("utf-8")).hexdigest()[:16]
                out.append(PoolSentence(
                    sentence_id=sid, doc_id=doc["doc_id"], source=doc["source"],
                    text=sent, tokens=len(sent.split()),
                    provenance=doc.get("provenance", ""),
                ))
                stats["kept"] += 1
    return out, stats
