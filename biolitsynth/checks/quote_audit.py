"""Independent audit of stored evidence quotes (plan §5.5), and the Gate 1 quote statistics.

The extractor already verified every quote; this re-checks the stored result with a plain slice comparison, so a bug
in the extractor or the store cannot slip an unverified quote into the evidence table.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from ..agents.extractor import QUOTE_MAX_CHARS, QUOTE_MIN_CHARS
from ..schemas import EvidenceItem, Paper

_OK = ("exact", "normalized", "fuzzy")
_FAILED = ("not_found", "too_short", "too_long")


class QuoteAuditError(RuntimeError):
    pass


@dataclass(frozen=True)
class QuoteStats:
    proposed: int
    first_try_ok: int
    exact: int
    normalized: int
    fuzzy: int
    retried: int
    recovered: int
    dropped: int
    stored: int

    @property
    def first_try_rate(self) -> float | None:
        return self.first_try_ok / self.proposed if self.proposed else None


def audit_spans(items: Sequence[EvidenceItem], papers: Mapping[str, Paper]) -> list[str]:
    """Ids of items whose span is invalid: paper missing, offsets None, abstract[start:end] != quote, or the
    quote shorter than QUOTE_MIN_CHARS / longer than QUOTE_MAX_CHARS."""
    bad = []
    for it in items:
        paper = papers.get(it.pmid)
        if (paper is None or it.quote_start is None or it.quote_end is None
                or paper.abstract[it.quote_start:it.quote_end] != it.quote
                or not QUOTE_MIN_CHARS <= len(it.quote) <= QUOTE_MAX_CHARS):
            bad.append(it.id)
    return bad


def quote_stats(log_rows: Iterable[Mapping], stored: int) -> QuoteStats:
    rows = list(log_rows)
    first = [r["status"] for r in rows if r["attempt"] == 1]
    recovered = sum(1 for r in rows if r["attempt"] == 2 and r["status"] in _OK)
    retried = sum(1 for s in first if s in _FAILED)
    return QuoteStats(proposed=len(first), first_try_ok=sum(1 for s in first if s in _OK),
                      exact=first.count("exact"), normalized=first.count("normalized"), fuzzy=first.count("fuzzy"),
                      retried=retried, recovered=recovered, dropped=retried - recovered, stored=stored)
