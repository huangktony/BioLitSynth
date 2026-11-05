"""Run a graph end to end: create the run, invoke with thread_id=run_id, export the trace."""
from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from ..config import Settings
from ..llm import CallBudget, GeminiClient, LLMClient, OpenAICompatClient, StageLLM
from ..pubmed import PubMedClient, TokenBucket
from ..schemas import Facet, ResearchPlan, SubQuestion
from ..store import Database
from ..trace import Tracer
from .build import build_graph
from .nodes import Deps
from .state import RunState


@dataclass
class RunSummary:
    run_id: str
    run_dir: Path
    state: dict
    stage_counts: Counter
    n_events: int
    trace_path: Path


def new_run_id() -> str:
    return f"run_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"


def initial_state(run_id: str, question: str, *, dry: bool, force_loops: bool = False,
                  approve_queries: bool = False) -> RunState:
    return RunState(
        run_id=run_id, question=question, dry=dry, approve_queries=approve_queries,
        dry_force_gap=force_loops, dry_force_retry=force_loops, dry_force_refine=force_loops,
        coverage_rounds=0, synth_attempt=-1,
        query_ids=[], fetched_query_ids=[], pmids=[], kept_pmids=[], extracted_pmids=[], evidence_ids=[],
    )


async def run_dry(question: str, settings: Settings, *, force_loops: bool = True,
                  run_id: str | None = None) -> RunSummary:
    """P0 acceptance: walk the whole graph, including one forced retry loop, and write an empty run + trace."""
    run_id = run_id or new_run_id()
    data_dir, run_dir = settings.paths.data_dir, settings.paths.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    db = Database(data_dir / "biolitsynth.db")
    try:
        db.create_run(run_id, question, settings)
        deps = Deps(settings=settings, db=db, tracer=Tracer(db, run_id), run_dir=run_dir)
        # Separate file so graph internals never mix with domain tables (plan §8.1 rule 6).
        async with AsyncSqliteSaver.from_conn_string(str(data_dir / "checkpoints.db")) as saver:
            graph = build_graph(deps, saver)
            try:
                state = await graph.ainvoke(
                    initial_state(run_id, question, dry=True, force_loops=force_loops),
                    config={"recursion_limit": settings.graph.recursion_limit,
                            "configurable": {"thread_id": run_id}},
                )
            except BaseException:
                db.set_run_status(run_id, "failed")
                raise
        db.set_run_status(run_id, "completed_dry")
        trace_path = run_dir / "trace.jsonl"
        n = db.export_trace_jsonl(run_id, trace_path)
        stages = Counter(r["stage"] for r in db.trace_events(run_id))
        return RunSummary(run_id, run_dir, dict(state), stages, n, trace_path)
    finally:
        db.close()


# ---------------------------------------------------------------------------------------------
# live runs (P2): real Query Builder + fetch + dedup; the run stops after dedup because later stages are stubs
# ---------------------------------------------------------------------------------------------
_EFFORT = {2: "simple", 3: "moderate", 4: "complex"}
LIVE_STOP_AFTER = "dedup"


def manual_plan(run_id: str, question: str, specs: list[str]) -> ResearchPlan:
    """Sub-questions given by hand, each "text" or "text | concept; concept". Replaces the Lead's plan call."""
    if not 2 <= len(specs) <= 4:
        raise ValueError(f"give 2 to 4 sub-questions, got {len(specs)}")
    sqs = []
    for i, spec in enumerate(specs):
        text, _, concepts = spec.partition("|")
        if not text.strip():
            raise ValueError(f"sub-question {i + 1} is empty")
        sqs.append(SubQuestion(id=f"sq{i + 1}", text=text.strip(), facet=Facet.OTHER, rationale="given by hand",
                               key_concepts=[c.strip() for c in concepts.split(";") if c.strip()]))
    return ResearchPlan(run_id=run_id, question=question, effort=_EFFORT[len(sqs)], sub_questions=sqs)


_ROLES = (("lead", "plan", "lead"), ("query", "query", "query_builder"))   # real agents so far: (role, stage, agent)


def resolve_clients(settings: Settings, clients: dict[str, LLMClient] | None = None, *,
                    roles: tuple[str, ...]) -> dict[str, LLMClient]:
    """Provider client per role, built from config.yaml + environment unless overridden. Fails before any run
    state is written if a model id is still a placeholder or a key is missing."""
    out = {r: c for r, c in (clients or {}).items() if r in roles}
    for role in roles:
        if role not in out:
            ref = getattr(settings.models, role)
            if ref.provider == "gemini":
                out[role] = GeminiClient(ref.model, settings.providers.gemini)
            elif ref.provider == "openai_compat":
                out[role] = OpenAICompatClient(ref.model, settings.providers.openai_compat)
            else:
                raise NotImplementedError(f"provider {ref.provider!r} for models.{role} has no client")
    return out


def make_stage_llms(settings: Settings, db: Database, tracer: Tracer, *, roles: tuple[str, ...],
                    clients: dict[str, LLMClient] | None = None,
                    limiters: dict[str, TokenBucket] | None = None) -> dict[str, StageLLM]:
    """StageLLMs for the roles that have real agents so far. `clients` overrides the provider client per role
    (tests pass a fake); otherwise the client is built from config.yaml and the environment. `limiters` overrides
    the per-provider rate limiter (tests pass a fast one)."""
    budget = CallBudget(settings.budget.max_llm_calls_per_run)
    sem = asyncio.Semaphore(settings.concurrency.llm)
    limiters = dict(limiters or {})
    clients = resolve_clients(settings, clients, roles=roles)
    out = {}
    for role, stage, agent in _ROLES:
        if role not in roles:
            continue
        ref, client = getattr(settings.models, role), clients[role]
        if ref.provider not in limiters:
            limiters[ref.provider] = TokenBucket(getattr(settings.providers, ref.provider).rpm / 60)
        out[role] = StageLLM(client, model=ref.model, stage=stage, agent=agent, db=db, tracer=tracer,
                             limiter=limiters[ref.provider], semaphore=sem, budget=budget)
    return out


@dataclass
class LiveSummary:
    run_id: str
    run_dir: Path
    state: dict
    plan: ResearchPlan
    queries: list[dict]
    papers_kept: int
    papers_dropped: int
    llm_calls: int
    llm_cache_hits: int
    network_calls: int
    trace_path: Path


async def run_live(question: str, sub_questions: list[str] | None, settings: Settings, *,
                   clients: dict[str, LLMClient] | None = None, limiters: dict[str, TokenBucket] | None = None,
                   transport=None, ncbi_env=None, ncbi_limiter: TokenBucket | None = None,
                   run_id: str | None = None) -> LiveSummary:
    """Question → Lead plan (or hand-given sub-questions) → LLM Query Builder (esearch-refined) → fetch → dedup,
    then stop."""
    run_id = run_id or new_run_id()
    data_dir, run_dir = settings.paths.data_dir, settings.paths.runs_dir / run_id
    plan = manual_plan(run_id, question, sub_questions) if sub_questions else None   # validate before writing
    roles = ("query",) if plan else ("lead", "query")
    clients = resolve_clients(settings, clients, roles=roles)
    run_dir.mkdir(parents=True, exist_ok=True)
    db = Database(data_dir / "biolitsynth.db")
    pubmed = None
    try:
        db.create_run(run_id, question, settings)
        tracer = Tracer(db, run_id)
        llms = make_stage_llms(settings, db, tracer, roles=roles, clients=clients, limiters=limiters)
        pubmed = PubMedClient(settings.ncbi, db, data_dir / "cache" / "pubmed", tracer=tracer,
                              concurrency=settings.concurrency.ncbi, transport=transport, env=ncbi_env,
                              limiter=ncbi_limiter)
        deps = Deps(settings=settings, db=db, tracer=tracer, run_dir=run_dir, pubmed=pubmed, llms=llms,
                    manual_plan=plan)
        async with AsyncSqliteSaver.from_conn_string(str(data_dir / "checkpoints.db")) as saver:
            graph = build_graph(deps, saver, interrupt_after=[LIVE_STOP_AFTER])
            try:
                state = await graph.ainvoke(
                    initial_state(run_id, question, dry=False),
                    config={"recursion_limit": settings.graph.recursion_limit,
                            "configurable": {"thread_id": run_id}},
                )
            except BaseException:
                db.set_run_status(run_id, "failed")
                raise
        db.set_run_status(run_id, f"stopped_after_{LIVE_STOP_AFTER}")
        trace_path = run_dir / "trace.jsonl"
        db.export_trace_jsonl(run_id, trace_path)
        queries = [dict(r) for r in db.query(
            "SELECT id, sub_question_id, query_string, hit_count, refinement_history_json FROM queries "
            "WHERE run_id = ? ORDER BY CAST(substr(id, 2) AS INTEGER)", (run_id,))]
        llm_events = [r for r in db.trace_events(run_id) if r["event_type"] == "llm_call"]
        dropped = len(state.get("dropped_pmids", []))
        return LiveSummary(
            run_id=run_id, run_dir=run_dir, state=dict(state), plan=db.get_plan(run_id), queries=queries,
            papers_kept=len(set(state.get("pmids", [])) - set(state.get("dropped_pmids", []))),
            papers_dropped=dropped, llm_calls=sum(1 for r in llm_events if not r["cache_hit"]),
            llm_cache_hits=sum(1 for r in llm_events if r["cache_hit"]), network_calls=pubmed.network_calls,
            trace_path=trace_path)
    finally:
        if pubmed:
            await pubmed.aclose()
        db.close()
