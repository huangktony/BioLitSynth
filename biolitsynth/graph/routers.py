"""Routing as pure functions of state (plan §8.1 rule 3). No LLM, no I/O, no tracing.

Two kinds live here:
  * route_*  -> name the next node.
  * fan_out_* -> return `Send`s for parallel work, or a plain node name when there is nothing to fan out
    over. That fallback matters: a conditional edge that returns [] ends the run silently.
Limits are arguments, bound once in build.py, so these stay testable without a graph or a config file.
"""
from __future__ import annotations

from typing import Any

from langgraph.types import Send

from .state import RunState


def _flags(state: RunState) -> dict[str, Any]:
    return {k: state.get(k, False) for k in ("dry", "dry_force_refine")}


# ---------------------------------------------------------------------------------------------
# fan-out
# ---------------------------------------------------------------------------------------------
def fan_out_queries(state: RunState) -> list[Send] | str:
    """One query builder per sub-question; in a gap round, only the gap sub-questions.

    Query ids are allocated here from len(query_ids), so they are unique across rounds without a counter.
    """
    targets = state.get("gap_sub_question_ids") or state.get("sub_question_ids") or []
    if not targets:
        return "approve_queries"
    taken = len(state.get("query_ids", []))
    return [
        Send("query_builder", {
            "query_id": f"q{taken + i + 1}",
            "sub_question_id": sq,
            "round": state.get("coverage_rounds", 0),
            "parent_span_id": state.get("dispatch_span_id"),
            **_flags(state),
        })
        for i, sq in enumerate(targets)
    ]


def fan_out_fetch(state: RunState) -> list[Send] | str:
    """One fetcher per query not yet fetched (idempotent across rounds)."""
    done = set(state.get("fetched_query_ids", []))
    pending = [q for q in state.get("query_ids", []) if q not in done]
    if not pending:
        return "dedup"
    return [Send("fetch", {"query_id": q, "parent_span_id": state.get("dispatch_span_id")}) for q in pending]


def fan_out_extract(state: RunState, *, batch_size: int) -> list[Send] | str:
    """One extractor per batch of kept PMIDs that have not been extracted yet."""
    done = set(state.get("extracted_pmids", []))
    pending = [p for p in state.get("kept_pmids", []) if p not in done]
    if not pending:
        return "quote_check"
    rnd = state.get("coverage_rounds", 0)
    return [
        Send("extract", {
            "batch_id": f"r{rnd}b{i // batch_size + 1}",
            "pmids": pending[i:i + batch_size],
            "parent_span_id": state.get("dispatch_span_id"),
        })
        for i in range(0, len(pending), batch_size)
    ]


# ---------------------------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------------------------
def route_after_count(state: dict, *, min_hits: int, max_hits: int, max_refinements: int) -> str:
    """Query Builder loop: refine until the hit count is inside the target band or attempts run out."""
    hits = state.get("hit_count")
    if hits is not None and min_hits <= hits <= max_hits:
        return "finalize"
    if state.get("refinements", 0) >= max_refinements:
        return "finalize"
    return "refine"


def route_after_coverage(state: RunState, *, max_extra_rounds: int) -> str:
    """The coverage node only fills gap_sub_question_ids when it is launching a round, and bumps
    coverage_rounds when it does, so launched rounds are always <= max. The `<=` check is a second
    barrier in case the node ever regresses (rule: loops are bounded twice)."""
    if state.get("gap_sub_question_ids") and state.get("coverage_rounds", 0) <= max_extra_rounds:
        return "dispatch_queries"
    return "cluster_studies"


def route_after_checks(state: RunState) -> str:
    """Claims that failed deterministic checks skip the (paid) critic. If none are left to critique,
    go straight to the router node."""
    pending = set(state.get("claim_ids", [])) - set(state.get("hard_failed_claim_ids", []))
    return "critique" if pending else "route"


def route_after_critic(state: RunState, *, max_retries: int) -> str:
    """Retry the Synthesizer for failed claims at most `max_retries` times.

    synth_attempt is the index of the latest synthesis (first = 0), so with max_retries=2 attempts 0 and 1
    can retry and attempt 2 cannot: three syntheses total, two of them retries.
    """
    if state.get("failed_claim_ids") and state.get("synth_attempt", 0) < max_retries:
        return "synthesize"
    return "assemble"
