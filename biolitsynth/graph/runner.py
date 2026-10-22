"""Run a graph end to end: create the run, invoke with thread_id=run_id, export the trace."""
from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from ..config import Settings
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
