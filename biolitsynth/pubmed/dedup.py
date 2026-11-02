"""Dedup + retraction filter (plan §5.4). Pure code over already-stored papers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

from ..schemas import Paper
from .parser import RETRACTED_TYPE

# "Retraction of Publication" is the name the plan uses; PubMed's current type for a notice is
# "Retraction Notice" (verified against live efetch output). Both are listed so neither era leaks through.
NOTICE_TYPES = frozenset({"Retraction Notice", "Retraction of Publication", "Published Erratum", "Comment"})


@dataclass(frozen=True)
class Dropped:
    pmid: str
    reason: Literal["retracted", "notice"]
    detail: str

    @property
    def log_key(self) -> str:
        return "dedup.retracted_dropped" if self.reason == "retracted" else "dedup.notice_dropped"


@dataclass
class DedupResult:
    papers: dict[str, Paper]      # kept, merged by PMID, retrieval provenance filled in
    dropped: list[Dropped]


def dedup_papers(entries: Iterable[tuple[str, int, str]], papers: dict[str, Paper]) -> DedupResult:
    """`entries` are (pmid, query_id, rank) rows from every query so far. A PMID found by several queries
    becomes one Paper with `retrieved_by` and `rank_by_query` unioned. Entries whose PMID has no stored paper
    are ignored (papers without abstracts are never stored)."""
    retrieved_by: dict[str, list[str]] = {}
    ranks: dict[str, dict[str, int]] = {}
    for pmid, qid, rank in entries:
        if pmid in papers:
            if qid not in retrieved_by.setdefault(pmid, []):
                retrieved_by[pmid].append(qid)
            ranks.setdefault(pmid, {})[qid] = rank

    kept: dict[str, Paper] = {}
    dropped: list[Dropped] = []
    for pmid in retrieved_by:
        paper = papers[pmid]
        types = set(paper.publication_types)
        if paper.is_retracted or RETRACTED_TYPE in types:
            dropped.append(Dropped(pmid, "retracted", f"types={sorted(types)}"))
        elif hit := sorted(types & NOTICE_TYPES):
            dropped.append(Dropped(pmid, "notice", f"types={hit}"))
        else:
            kept[pmid] = paper.model_copy(update={"retrieved_by": retrieved_by[pmid], "rank_by_query": ranks[pmid]})
    return DedupResult(kept, dropped)
