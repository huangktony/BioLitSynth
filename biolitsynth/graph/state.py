"""RunState (plan §8.1 rule 2): IDs and counters only. SQLite is the single source of truth.

Reducer rules, because this is where LangGraph code usually goes wrong:
  * A key written by PARALLEL branches (Send fan-out) needs a reducer, or LangGraph raises
    InvalidUpdateError. Those keys use `merge_unique`, so a retry or gap round can never duplicate an ID.
  * Every other key has exactly one writer per superstep and is overwritten (no reducer). Never make a
    counter or a "current failures" list append-only.
"""
from __future__ import annotations

from typing import Annotated, TypedDict


def merge_unique(left: list[str] | None, right: list[str] | None) -> list[str]:
    """Order-preserving union. Safe under concurrent branches and under re-execution."""
    out: list[str] = []
    seen: set[str] = set()
    for item in [*(left or []), *(right or [])]:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


UniqueIds = Annotated[list[str], merge_unique]


class RunState(TypedDict, total=False):
    # identity
    run_id: str
    question: str

    # --- parallel-written (reducer: merge_unique) ---
    query_ids: UniqueIds            # every query ever built this run, all rounds
    fetched_query_ids: UniqueIds    # queries whose fetch finished
    pmids: UniqueIds                # candidate PMIDs
    kept_pmids: UniqueIds           # survived rerank
    extracted_pmids: UniqueIds      # already sent through the extractor
    evidence_ids: UniqueIds

    # --- single-writer (overwritten) ---
    sub_question_ids: list[str]
    gap_sub_question_ids: list[str]   # sub-questions a gap round will re-query; [] = no gap round
    unresolved_gap_ids: list[str]     # gaps left after the round cap; become GAP claims
    conflict_ids: list[str]
    claim_ids: list[str]              # current claims (latest attempt of each)
    hard_failed_claim_ids: list[str]  # failed deterministic checks
    critic_failed_claim_ids: list[str]
    failed_claim_ids: list[str]       # hard ∪ critic failures: what the Synthesizer must redo
    dispatch_span_id: str             # trace span that parents the next fan-out

    # --- counters (bounded twice: here and by recursion_limit) ---
    coverage_rounds: int              # extra retrieval rounds already launched
    synth_attempt: int                # attempt index of the latest synthesis; -1 before the first
    status: str

    # --- switches ---
    dry: bool
    approve_queries: bool
    dry_force_gap: bool               # dry-run only: exercise the gap-round loop
    dry_force_retry: bool             # dry-run only: exercise the synth↔critic loop
    dry_force_refine: bool            # dry-run only: exercise query refinement
