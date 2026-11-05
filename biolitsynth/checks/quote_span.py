"""Quote Span Validator (plan §5.5): find an extractor's quote in the abstract and set exact offsets.

Order: exact substring → match after normalization (NFKC, dash and quote variants, whitespace runs) → fuzzy
(rapidfuzz partial_ratio ≥ threshold, quotes of at least FUZZY_MIN_CHARS only). Normalized and fuzzy matches are
mapped back through a per-character index map to positions in the ORIGINAL abstract, and the stored quote becomes
exactly `abstract[start:end]`, so the `quote_in_abstract` check can be a plain slice comparison.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz

from ..schemas import EvidenceItem

FUZZY_THRESHOLD = 95
FUZZY_MIN_CHARS = 20          # below this, a 95% fuzzy match is one or two characters off a short phrase: too risky
_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe63\uff0d"), "-")
_QUOTES = {ord(c): "'" for c in "‘’‚‛′"} | {ord(c): '"' for c in "“”„‟″"}

Status = Literal["exact", "normalized", "fuzzy", "not_found"]


@dataclass(frozen=True)
class QuoteMatch:
    status: Status
    start: int | None = None
    end: int | None = None
    quote: str | None = None      # abstract[start:end], the text to store
    score: float | None = None    # fuzzy score, for the trace

    @property
    def found(self) -> bool:
        return self.status != "not_found"


def normalize(text: str) -> tuple[str, list[int]]:
    """Normalized text and, for each of its characters, the index of the original character it came from."""
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKC", ch).translate(_DASHES).translate(_QUOTES):
            if c == "­":
                continue
            if c.isspace():
                if not out or out[-1] == " ":
                    continue
                c = " "
            out.append(c)
            idx.append(i)
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def _span(text: str, idx: list[int], a: int, b: int) -> tuple[int, int]:
    """Original-text span for normalized positions [a, b), without whitespace at either edge."""
    s, e = idx[a], idx[b - 1] + 1
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return s, e


def validate_quote(abstract: str, quote: str, *, threshold: float = FUZZY_THRESHOLD) -> QuoteMatch:
    q = quote.strip()
    if not q:
        return QuoteMatch("not_found")
    i = abstract.find(q)
    if i >= 0:
        return QuoteMatch("exact", i, i + len(q), q)
    na, ia = normalize(abstract)
    nq, _ = normalize(q)
    j = na.find(nq)
    if j >= 0:
        s, e = _span(abstract, ia, j, j + len(nq))
        return QuoteMatch("normalized", s, e, abstract[s:e])
    if len(nq) >= FUZZY_MIN_CHARS and len(nq) <= len(na):
        al = fuzz.partial_ratio_alignment(nq, na)
        if al is not None and al.score >= threshold and al.dest_end > al.dest_start:
            s, e = _span(abstract, ia, al.dest_start, al.dest_end)
            return QuoteMatch("fuzzy", s, e, abstract[s:e], al.score)
    return QuoteMatch("not_found")


def apply_span(item: EvidenceItem, abstract: str, *, threshold: float = FUZZY_THRESHOLD) -> tuple[EvidenceItem | None, QuoteMatch]:
    """The item with offsets set and its quote snapped to the abstract text, or None if the quote isn't there.
    The caller logs `validation.quote_snapped` for fuzzy matches and re-extracts once before dropping (plan §5.5)."""
    m = validate_quote(abstract, item.quote, threshold=threshold)
    if not m.found:
        return None, m
    return item.model_copy(update={"quote": m.quote, "quote_start": m.start, "quote_end": m.end}), m
