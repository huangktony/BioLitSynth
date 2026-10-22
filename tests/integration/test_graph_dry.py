"""P0 acceptance: the whole graph runs on stubs, every bounded loop fires once, and nothing duplicates."""
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from biolitsynth.graph import Deps, build_graph, initial_state, run_dry
from biolitsynth.graph.runner import new_run_id
from biolitsynth.store import Database
from biolitsynth.trace import Tracer

Q = "Does metformin reduce cancer risk?"
LIST_KEYS = ("query_ids", "fetched_query_ids", "pmids", "kept_pmids", "extracted_pmids", "evidence_ids",
             "claim_ids", "sub_question_ids")


@pytest.fixture
def harness(settings):
    run_id = new_run_id()
    run_dir = settings.paths.runs_dir / run_id
    db = Database(settings.paths.data_dir / "t.db")
    deps = Deps(settings=settings, db=db, tracer=Tracer(db, run_id), run_dir=run_dir)
    db.create_run(run_id, Q, settings)
    graph = build_graph(deps, InMemorySaver())
    cfg = {"recursion_limit": settings.graph.recursion_limit, "configurable": {"thread_id": run_id}}
    yield run_id, db, graph, cfg
    db.close()


def stages(db, run_id, **where):
    return [e for e in db.trace_events(run_id) if all(e[k] == v for k, v in where.items())]


async def test_forced_loops_fire_exactly_once(harness):
    run_id, db, graph, cfg = harness
    s = await graph.ainvoke(initial_state(run_id, Q, dry=True, force_loops=True), cfg)

    assert s["status"] == "assembled"
    assert s["coverage_rounds"] == 1                       # one gap round, not two
    assert s["synth_attempt"] == 1                         # initial + one forced retry
    assert s["query_ids"] == ["q1", "q2", "q3", "q4"]      # 3 initial + 1 gap-round query, no id reuse
    assert s["failed_claim_ids"] == []                     # the retry fixed it


async def test_no_state_list_ever_contains_duplicates(harness):
    run_id, db, graph, cfg = harness
    s = await graph.ainvoke(initial_state(run_id, Q, dry=True, force_loops=True), cfg)
    for k in LIST_KEYS:
        assert len(s[k]) == len(set(s[k])), f"{k} has duplicates: {s[k]}"


async def test_gap_round_extracts_only_new_papers(harness):
    run_id, db, graph, cfg = harness
    s = await graph.ainvoke(initial_state(run_id, Q, dry=True, force_loops=True), cfg)
    batches = stages(db, run_id, stage="extract", agent="extractor")
    assert [e["message"].split(":")[0] for e in batches] == ["batch r0b1", "batch r0b2", "batch r0b3", "batch r1b1"]
    assert len(s["evidence_ids"]) == len(s["extracted_pmids"]) == 16     # 12 first round + 4 gap round
    assert len(s["kept_pmids"]) == 16


async def test_query_refinement_is_recorded_and_bounded(harness):
    run_id, db, graph, cfg = harness
    await graph.ainvoke(initial_state(run_id, Q, dry=True, force_loops=True), cfg)
    retries = stages(db, run_id, event_type="retry", stage="query")
    assert len(retries) == 4                                # every query was forced out of band once
    for row in db.query("SELECT * FROM queries WHERE run_id = ?", (run_id,)):
        hist = json.loads(row["refinement_history_json"])
        assert [h[1] for h in hist] == [0, 150]             # count -> refine -> count, then stop


async def test_retry_regenerates_only_failed_claims_and_keeps_attempt_history(harness):
    run_id, db, graph, cfg = harness
    await graph.ainvoke(initial_state(run_id, Q, dry=True, force_loops=True), cfg)
    rows = db.query("SELECT id, attempt FROM claims WHERE run_id = ? ORDER BY attempt, id", (run_id,))
    assert [(r["id"], r["attempt"]) for r in rows] == [("cl1", 0), ("cl2", 0), ("cl1", 1)]   # cl2 frozen


async def test_without_forcing_no_loop_fires(harness):
    run_id, db, graph, cfg = harness
    s = await graph.ainvoke(initial_state(run_id, Q, dry=True, force_loops=False), cfg)
    assert (s["coverage_rounds"], s["synth_attempt"], len(s["query_ids"])) == (0, 0, 3)
    assert not stages(db, run_id, event_type="retry")


async def test_every_stage_in_the_plan_appears_in_the_trace(harness):
    run_id, db, graph, cfg = harness
    await graph.ainvoke(initial_state(run_id, Q, dry=True, force_loops=True), cfg)
    seen = {e["stage"] for e in db.trace_events(run_id)}
    assert seen == {"plan", "query", "approve_queries", "fetch", "dedup", "rerank", "extract", "coverage",
                    "cluster", "normalize_terms", "conflict", "synthesize", "cite", "check", "critique",
                    "route", "assemble"}


async def test_subgraph_spans_nest_under_the_dispatch_handoff(harness):
    run_id, db, graph, cfg = harness
    await graph.ainvoke(initial_state(run_id, Q, dry=True, force_loops=False), cfg)
    handoff = stages(db, run_id, stage="query", event_type="handoff")[0]
    inner = [e for e in stages(db, run_id, agent="query_builder")]
    assert inner and all(e["parent_span_id"] == handoff["span_id"] for e in inner)


async def test_plan_snapshot_and_plan_json_written(harness, settings):
    run_id, db, graph, cfg = harness
    await graph.ainvoke(initial_state(run_id, Q, dry=True), cfg)
    assert (settings.paths.runs_dir / run_id / "01_plan.json").exists()
    assert db.get_run(run_id)["plan_json"]


async def test_approve_queries_pauses_then_resumes(harness):
    run_id, db, graph, cfg = harness
    s = await graph.ainvoke(initial_state(run_id, Q, dry=True, approve_queries=True), cfg)
    assert "__interrupt__" in s                             # paused before fetching
    assert not stages(db, run_id, stage="fetch", agent="pubmed_fetcher")
    s = await graph.ainvoke(Command(resume=True), cfg)
    assert s["status"] == "assembled"
    assert len(s["query_ids"]) == len(set(s["query_ids"])) == 3


async def test_run_dry_end_to_end_writes_run_trace_and_status(settings):
    summary = await run_dry(Q, settings)
    assert summary.state["status"] == "assembled"
    assert summary.trace_path.exists()
    lines = [json.loads(l) for l in summary.trace_path.read_text().splitlines()]
    assert len(lines) == summary.n_events > 0 and "input_refs" in lines[0]
    db = Database(settings.paths.data_dir / "biolitsynth.db")
    assert db.get_run(summary.run_id)["status"] == "completed_dry"
    db.close()
    assert (settings.paths.data_dir / "checkpoints.db").exists()      # separate file from domain tables


async def test_recursion_limit_is_a_loud_backstop(harness):
    from langgraph.errors import GraphRecursionError
    run_id, db, graph, cfg = harness
    with pytest.raises(GraphRecursionError):
        await graph.ainvoke(initial_state(run_id, Q, dry=True, force_loops=True), {**cfg, "recursion_limit": 10})
