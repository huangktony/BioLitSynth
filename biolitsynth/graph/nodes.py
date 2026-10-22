"""LangGraph nodes (plan §8.1 rule 1): thin. Open a span, call a stage function, persist, return IDs only.

In P0 the stage functions are the stubs in dry.py. Replace them one phase at a time; nodes keep their shape.
Rule for state writes: a node that runs in parallel (query_builder, fetch, extract) may only return reducer
keys (see state.py). `parent_span_id` arrives in the Send payload; top-level nodes are trace roots.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..config import Settings
from ..schemas import Stage
from ..store import Database, write_snapshot
from ..trace import Tracer
from . import dry
from .routers import route_after_count
from .state import RunState, merge_unique

Node = Callable[..., Awaitable[dict]]


@dataclass
class Deps:
    settings: Settings
    db: Database
    tracer: Tracer
    run_dir: Path

    @property
    def run_id(self) -> str:
        return self.tracer.run_id


def _qnum(query_id: str) -> int:
    return int(query_id.removeprefix("q"))


# ---------------------------------------------------------------------------------------------
# Query Builder subgraph (plan §5.2, §8.1 rule 7): draft → count → (refine → count)* → finalize
# ---------------------------------------------------------------------------------------------
def build_query_builder(deps: Deps):
    from typing import TypedDict

    class QBState(TypedDict, total=False):
        query_id: str
        sub_question_id: str
        round: int
        parent_span_id: str | None
        dry_force_refine: bool
        query_string: str
        hit_count: int
        refinements: int
        history: list[tuple[str, int]]

    band = deps.settings.query_band
    max_ref = deps.settings.limits.query_refinement_attempts
    tr = deps.tracer

    async def draft(s: QBState) -> dict:
        async with tr.span("query", "query_builder", input_refs=[s["sub_question_id"]],
                           parent=s.get("parent_span_id")) as sp:
            qs = f"[dry] ({s['sub_question_id']} concepts)"
            sp.output_refs = [s["query_id"]]
            sp.message = f"{s['query_id']} drafted"
        return {"query_string": qs, "refinements": 0, "history": []}

    async def count(s: QBState) -> dict:
        hits = dry.dry_hit_count(s["refinements"], s.get("dry_force_refine", False))
        async with tr.span("query", "query_builder", event_type="tool_call", input_refs=[s["query_id"]],
                           parent=s.get("parent_span_id")) as sp:
            sp.message = f"{s['query_id']} esearch count (stub): {hits} hits"
        return {"hit_count": hits, "history": [*s["history"], (s["query_string"], hits)]}

    async def refine(s: QBState) -> dict:
        await tr.event("query", "query_builder", "retry", input_refs=[s["query_id"]],
                       parent=s.get("parent_span_id"), status="retry",
                       message=f"{s['query_id']} refinement {s['refinements'] + 1}/{max_ref}: "
                               f"{s['hit_count']} hits outside {band.min_hits}-{band.max_hits}")
        return {"refinements": s["refinements"] + 1, "query_string": s["query_string"] + " [refined]"}

    async def finalize(s: QBState) -> dict:
        deps.db.insert("queries", {
            "run_id": deps.run_id, "id": s["query_id"], "sub_question_id": s["sub_question_id"],
            "query_string": s["query_string"], "filters_json": "{}", "hit_count": s["hit_count"],
            "refinement_history_json": json.dumps(s["history"]), "round": s.get("round", 0),
        }, replace=True)
        return {}

    g = StateGraph(QBState)
    for name, fn in (("draft", draft), ("count", count), ("refine", refine), ("finalize", finalize)):
        g.add_node(name, fn)
    g.add_edge(START, "draft")
    g.add_edge("draft", "count")
    g.add_conditional_edges(
        "count",
        lambda s: route_after_count(s, min_hits=band.min_hits, max_hits=band.max_hits, max_refinements=max_ref),
        ["refine", "finalize"],
    )
    g.add_edge("refine", "count")
    g.add_edge("finalize", END)
    return g.compile()


# ---------------------------------------------------------------------------------------------
# main graph nodes
# ---------------------------------------------------------------------------------------------
def make_nodes(deps: Deps) -> dict[str, Node]:
    st, db, tr = deps.settings, deps.db, deps.tracer
    qb = build_query_builder(deps)

    def noop(stage: Stage, agent: str, message: str, event_type="tool_call") -> Node:
        async def node(state: RunState) -> dict:
            async with tr.span(stage, agent, event_type=event_type) as sp:
                sp.message = message
            return {}
        return node

    async def plan(state: RunState) -> dict:
        async with tr.span("plan", "lead", input_refs=[deps.run_id]) as sp:
            p = dry.dry_plan(deps.run_id, state["question"], st.limits.sub_questions)
            db.save_plan(p)
            write_snapshot(deps.run_dir, 1, "plan", p)
            sp.output_refs = [s.id for s in p.sub_questions]
            sp.message = f"{p.effort} plan, {len(p.sub_questions)} sub-questions (stub)"
        return {"sub_question_ids": sp.output_refs}

    async def dispatch_queries(state: RunState) -> dict:
        targets = state.get("gap_sub_question_ids") or state.get("sub_question_ids", [])
        sid = await tr.event("query", "lead", "handoff", input_refs=targets,
                             message="gap round" if state.get("gap_sub_question_ids") else "initial round")
        return {"dispatch_span_id": sid}

    async def query_builder(payload: dict) -> dict:
        out = await qb.ainvoke(payload)
        return {"query_ids": [out["query_id"]]}

    async def approve_queries(state: RunState) -> dict:
        gated = state.get("approve_queries") and state.get("coverage_rounds", 0) == 0
        await tr.event("approve_queries", "researcher" if gated else "pipeline", "decision",
                       input_refs=state.get("query_ids", []),
                       message="waiting for researcher approval" if gated else "skipped (not requested)")
        if gated:
            # Outside any span: interrupt() raises to pause the run. On resume this node re-executes.
            interrupt({"query_ids": state.get("query_ids", [])})
        return {}

    async def dispatch_fetch(state: RunState) -> dict:
        pending = [q for q in state.get("query_ids", []) if q not in state.get("fetched_query_ids", [])]
        return {"dispatch_span_id": await tr.event("fetch", "pipeline", "handoff", input_refs=pending)}

    async def fetch(payload: dict) -> dict:
        qid = payload["query_id"]
        async with tr.span("fetch", "pubmed_fetcher", input_refs=[qid], parent=payload.get("parent_span_id")) as sp:
            pmids = dry.dry_pmids(qid)
            sp.output_refs = [f"pmid:{p}" for p in pmids]
            sp.message = f"{qid}: {len(pmids)} synthetic candidates"
        return {"pmids": pmids, "fetched_query_ids": [qid]}

    async def rerank(state: RunState) -> dict:
        async with tr.span("rerank", "reranker", input_refs=[f"pmid:{p}" for p in state.get("pmids", [])]) as sp:
            sp.message = "keep all (stub; MedCPT arrives in P3)"
        return {"kept_pmids": state.get("pmids", []), "dispatch_span_id": sp.span_id}

    async def extract(payload: dict) -> dict:
        pmids = payload["pmids"]
        async with tr.span("extract", "extractor", input_refs=[f"pmid:{p}" for p in pmids],
                           parent=payload.get("parent_span_id")) as sp:
            ids = [f"ev_{p}_1" for p in pmids]
            sp.output_refs = ids
            sp.message = f"batch {payload['batch_id']}: {len(pmids)} abstracts (stub)"
        return {"evidence_ids": ids, "extracted_pmids": pmids}

    async def coverage(state: RunState) -> dict:
        rounds = state.get("coverage_rounds", 0)
        gaps = dry.dry_gap(state.get("sub_question_ids", []), rounds, st.limits.coverage_extra_rounds,
                           state.get("dry_force_gap", False))
        async with tr.span("coverage", "lead", input_refs=state.get("sub_question_ids", [])) as sp:
            sp.output_refs = gaps
            sp.message = f"gap round {rounds + 1} for {gaps}" if gaps else "coverage OK / round cap reached"
        return {"gap_sub_question_ids": gaps, "coverage_rounds": rounds + (1 if gaps else 0)}

    async def synthesize(state: RunState) -> dict:
        attempt = state.get("synth_attempt", -1) + 1
        redo = state.get("failed_claim_ids") if attempt > 0 else None
        async with tr.span("synthesize", "synthesizer", input_refs=redo or state.get("sub_question_ids", [])) as sp:
            claims = dry.dry_claims(attempt, redo, state.get("sub_question_ids", []))
            for c in claims:
                db.insert("claims", {
                    "run_id": deps.run_id, "id": c.id, "attempt": attempt, "text": c.text,
                    "claim_type": c.claim_type.value, "confidence": c.confidence, "conflict_id": c.conflict_id,
                    "reasoning_json": "[]", "caveats_json": "[]", "status": None,
                }, replace=True)
            write_snapshot(deps.run_dir, 6, "claims", claims, attempt=attempt)
            sp.output_refs = [c.id for c in claims]
            sp.message = f"attempt {attempt}: {'regenerated ' + str(redo) if redo else 'initial draft'} (stub)"
        all_ids = merge_unique(state.get("claim_ids", []), [c.id for c in claims])
        return {"claim_ids": all_ids, "synth_attempt": attempt, "failed_claim_ids": [],
                "hard_failed_claim_ids": [], "critic_failed_claim_ids": []}

    async def det_checks(state: RunState) -> dict:
        async with tr.span("check", "deterministic_checker", event_type="validation",
                           input_refs=state.get("claim_ids", [])) as sp:
            sp.message = "all checks pass (stub; §5.9 arrives in P6)"
        return {"hard_failed_claim_ids": []}

    async def critique(state: RunState) -> dict:
        pending = [c for c in state.get("claim_ids", []) if c not in state.get("hard_failed_claim_ids", [])]
        failed = pending[:1] if state.get("dry_force_retry") and state.get("synth_attempt", 0) == 0 else []
        async with tr.span("critique", "critic", input_refs=pending) as sp:
            sp.output_refs = failed
            sp.message = f"{len(failed)} claim(s) FAIL (stub)"
        return {"critic_failed_claim_ids": failed}

    async def route(state: RunState) -> dict:
        from .routers import route_after_critic
        failed = merge_unique(state.get("hard_failed_claim_ids", []), state.get("critic_failed_claim_ids", []))
        nxt = route_after_critic({**state, "failed_claim_ids": failed}, max_retries=st.limits.synth_retries)
        await tr.event("route", "router", "decision", input_refs=failed, message=f"-> {nxt}",
                       status="retry" if nxt == "synthesize" else "ok")
        return {"failed_claim_ids": failed}

    async def assemble(state: RunState) -> dict:
        async with tr.span("assemble", "report_assembler", input_refs=state.get("claim_ids", [])) as sp:
            sp.message = "empty report (stub; assembler arrives in P5)"
        return {"status": "assembled"}

    return {
        "plan": plan,
        "dispatch_queries": dispatch_queries,
        "query_builder": query_builder,
        "approve_queries": approve_queries,
        "dispatch_fetch": dispatch_fetch,
        "fetch": fetch,
        "dedup": noop("dedup", "dedup", "no-op (stub; dedup + retraction filter arrive in P1)"),
        "rerank": rerank,
        "extract": extract,
        "quote_check": noop("extract", "quote_span_validator", "no-op (stub)", event_type="validation"),
        "coverage": coverage,
        "cluster_studies": noop("cluster", "study_clusterer", "no-op (stub; arrives in P4)"),
        "normalize_terms": noop("normalize_terms", "term_normalizer", "no-op (stub; arrives in P4)"),
        "conflicts": noop("conflict", "conflict_detector", "no-op (stub; arrives in P4)"),
        "synthesize": synthesize,
        "cite": noop("cite", "citation_agent", "no-op (stub; arrives in P5)"),
        "det_checks": det_checks,
        "critique": critique,
        "route": route,
        "assemble": assemble,
    }
