"""Query Builder (plan §5.2): sub-question → PubMed query, refined against live hit counts.

The LLM writes query strings (QueryDraft). Code decides everything checkable: the hit count (esearch), whether to
refine and in which direction, when to stop, and which attempt becomes the final query.

These functions don't depend on LangGraph. The graph's query_builder subgraph calls them one step per node, and
`build_query` runs the same loop end to end for scripts and evals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..config import QueryBand
from ..llm import RenderedPrompt, StageLLM, load_prompt
from ..pubmed import PubMedClient, QueryRejected
from ..schemas import PubMedQuery, QueryDraft, SubQuestion

PROMPT = "query_builder"
Direction = Literal["broaden", "narrow"]


class InvalidDraft(ValueError):
    pass


def in_band(hits: int, band: QueryBand) -> bool:
    return band.min_hits <= hits <= band.max_hits


def refine_direction(hits: int, band: QueryBand) -> Direction | None:
    """None when the count is inside the band."""
    if hits < band.min_hits:
        return "broaden"
    if hits > band.max_hits:
        return "narrow"
    return None


def pick_final(history: list[tuple[str, int]], band: QueryBand) -> tuple[str, int]:
    """The attempt that becomes the query. First in-band attempt; otherwise the non-empty attempt closest to the band
    (by ratio, so 4,000 vs a 2,000 cap and 10 vs a 20 floor are equally far); if every attempt had 0 hits, the last."""
    if not history:
        raise ValueError("no attempts to choose from")
    for q, hits in history:
        if in_band(hits, band):
            return q, hits

    def distance(hits: int) -> float:
        return band.min_hits / hits if hits < band.min_hits else hits / band.max_hits

    nonzero = [(q, h) for q, h in history if h > 0]
    return min(nonzero, key=lambda a: distance(a[1])) if nonzero else history[-1]


def check_draft(d: QueryDraft) -> str:
    q = " ".join(d.query_string.split())
    if not q:
        raise InvalidDraft("the model returned an empty query string")
    if q.count("(") != q.count(")"):
        raise InvalidDraft(f"unbalanced parentheses in query: {q}")
    return q


def render_draft(sq: SubQuestion, band: QueryBand, *, version: int | None = None) -> RenderedPrompt:
    return load_prompt(PROMPT, version=version).render("draft", sub_question=sq, min_hits=band.min_hits, max_hits=band.max_hits)


def render_refine(sq: SubQuestion, history: list[tuple[str, int]], band: QueryBand, *, attempt: int,
                  max_attempts: int, version: int | None = None) -> RenderedPrompt:
    direction = refine_direction(history[-1][1], band)
    if direction is None:
        raise ValueError("last attempt is already in the band; nothing to refine")
    return load_prompt(PROMPT, version=version).render("refine", sub_question=sq, history=history, direction=direction,
                                      attempt=attempt, max_attempts=max_attempts,
                                      min_hits=band.min_hits, max_hits=band.max_hits)


async def draft_query(sq: SubQuestion, llm: StageLLM, band: QueryBand, *, version: int | None = None,
                      parent: str | None = None) -> str:
    d = await llm.complete(render_draft(sq, band, version=version), QueryDraft, input_refs=[sq.id], parent=parent)
    return check_draft(d)


async def refine_query(sq: SubQuestion, history: list[tuple[str, int]], llm: StageLLM, band: QueryBand, *,
                       attempt: int, max_attempts: int, version: int | None = None,
                       parent: str | None = None) -> str:
    prompt = render_refine(sq, history, band, attempt=attempt, max_attempts=max_attempts, version=version)
    d = await llm.complete(prompt, QueryDraft, input_refs=[sq.id], parent=parent)
    return check_draft(d)


async def count_hits(query: str, pubmed: PubMedClient, *, parent: str | None = None) -> int:
    """esearch hit count. A query PubMed refuses as malformed counts as 0 hits, so the loop broadens it."""
    try:
        return await pubmed.count(query, parent=parent)
    except QueryRejected:
        return 0


@dataclass
class BuiltQuery:
    query_string: str
    hit_count: int
    history: list[tuple[str, int]] = field(default_factory=list)


async def build_query(sq: SubQuestion, llm: StageLLM, pubmed: PubMedClient, band: QueryBand, *,
                      max_refinements: int, version: int | None = None, parent: str | None = None) -> BuiltQuery:
    """draft → count → (refine → count)* → pick, outside the graph (same steps as the subgraph)."""
    q = await draft_query(sq, llm, band, version=version, parent=parent)
    history = [(q, await count_hits(q, pubmed, parent=parent))]
    refinements = 0
    while not in_band(history[-1][1], band) and refinements < max_refinements:
        refinements += 1
        q = await refine_query(sq, history, llm, band, attempt=refinements, max_attempts=max_refinements,
                               version=version, parent=parent)
        history.append((q, await count_hits(q, pubmed, parent=parent)))
    final, hits = pick_final(history, band)
    return BuiltQuery(final, hits, history)


def to_pubmed_query(query_id: str, sq_id: str, built: BuiltQuery, *, retmax: int, keep_top: int) -> PubMedQuery:
    return PubMedQuery(id=query_id, sub_question_id=sq_id, query_string=built.query_string, hit_count=built.hit_count,
                       refinement_history=built.history, retmax_candidates=retmax, keep_top=keep_top)
