"""P1 acceptance through the real graph: non-dry fetch + dedup over recorded NCBI data, no live network.

Sub-questions are given by hand (manual plan) and a scripted LLM writes one query per sub-question. The mock NCBI
maps those query strings to hand-chosen PMID lists (every query reports 150 hits, inside the band, so nothing is
refined) so the expected numbers below can be worked out from the fixtures by hand:

  q1 (sq1): the 25 recorded metformin/CRC ids   -> 20 have abstracts, 5 are letters/no abstract
  q2 (sq2): CRC[0], CRC[1], 41911553 (retracted RCT), 39502250 (retracted), 41911555 (retracted, no abstract)
  q3 (sq3): 42964977 (retraction notice), 42930381 (erratum), 42722381 (letter, no abstract), CRC[0]
Stored with abstracts: 20 + 41911553 + 39502250 + 42964977 + 42930381 = 24.  Dropped by dedup: those last four.
"""
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from biolitsynth.graph import Deps, build_graph, initial_state
from biolitsynth.graph.runner import make_stage_llms, manual_plan, new_run_id
from biolitsynth.pubmed import PubMedClient, TokenBucket
from biolitsynth.store import Database
from biolitsynth.trace import Tracer

from ..llm_mock import ScriptedClient
from ..pubmed_mock import CRC_IDS, FakeNcbi

NO_ABSTRACT = {"28506970", "30385499", "32384400", "28506966", "21949227"}
RETRACTED, NOTICES = {"41911553", "39502250"}, {"42964977", "42930381"}
Q1, Q2, Q3 = "metformin AND colorectal neoplasms[mh]", "metformin AND rct[pt]", "metformin AND notices"
SEARCHES = {
    Q1: CRC_IDS,
    Q2: [CRC_IDS[0], CRC_IDS[1], "41911553", "39502250", "41911555"],
    Q3: ["42964977", "42930381", "42722381", CRC_IDS[0]],
}
SUBQ = ["metformin and colorectal cancer", "metformin trials", "metformin notices"]


def scripted():
    return ScriptedClient([(f"({sq}): ", {"query_string": q, "rationale": "scripted"})
                           for sq, q in (("sq1", Q1), ("sq2", Q2), ("sq3", Q3))])
KEPT = [i for i in CRC_IDS if i not in NO_ABSTRACT]


async def run_graph(settings, ncbi, *, with_client=True):
    run_id = new_run_id()
    run_dir = settings.paths.runs_dir / run_id
    db = Database(settings.paths.data_dir / "biolitsynth.db")
    tracer = Tracer(db, run_id)
    db.create_run(run_id, "q?", settings)
    client = PubMedClient(settings.ncbi, db, settings.paths.data_dir / "cache" / "pubmed", tracer=tracer,
                          transport=ncbi.transport, env={}, limiter=TokenBucket(10_000)) if with_client else None
    llms = make_stage_llms(settings, db, tracer, roles=("query",), clients={"query": scripted()},
                           limiters={"gemini": TokenBucket(10_000)})
    deps = Deps(settings=settings, db=db, tracer=tracer, run_dir=run_dir, pubmed=client, llms=llms,
                manual_plan=manual_plan(run_id, "q?", SUBQ))
    graph = build_graph(deps, InMemorySaver())
    cfg = {"recursion_limit": settings.graph.recursion_limit, "configurable": {"thread_id": run_id}}
    state = await graph.ainvoke(initial_state(run_id, "q?", dry=False), cfg)
    return run_id, run_dir, db, state, client


async def test_fetch_dedup_and_drops_over_three_queries(settings):
    ncbi = FakeNcbi(SEARCHES, total=150)
    run_id, run_dir, db, state, client = await run_graph(settings, ncbi)

    assert state["status"] == "assembled" and state["query_ids"] == ["q1", "q2", "q3"]
    assert set(state["dropped_pmids"]) == RETRACTED | NOTICES
    assert state["kept_pmids"] == KEPT or sorted(state["kept_pmids"]) == sorted(KEPT)
    assert not set(state["kept_pmids"]) & (RETRACTED | NOTICES)           # nothing dropped reaches extraction
    assert sorted(state["extracted_pmids"]) == sorted(KEPT)               # ...and only kept papers are extracted

    stored = {r["pmid"] for r in db.query("SELECT pmid FROM papers")}
    assert stored == set(KEPT) | RETRACTED | NOTICES and len(stored) == 24
    assert not stored & {"41911555", "42722381"} and not stored & NO_ABSTRACT          # no-abstract papers are not stored
    await client.aclose()


async def test_retrieval_provenance_is_unioned_across_queries(settings):
    _, run_dir, db, _, client = await run_graph(settings, FakeNcbi(SEARCHES, total=150))
    snap = {p["pmid"]: p for p in json.loads((run_dir / "03_papers.json").read_text())}
    assert len(snap) == 20
    shared = snap[CRC_IDS[0]]
    assert shared["retrieved_by"] == ["q1", "q2", "q3"]
    assert shared["rank_by_query"] == {"q1": 1, "q2": 1, "q3": 4}          # rank = position in each esearch result
    assert snap[CRC_IDS[1]]["retrieved_by"] == ["q1", "q2"]
    assert snap[CRC_IDS[5]]["retrieved_by"] == ["q1"]
    await client.aclose()


async def test_drops_and_skips_are_logged_with_the_plan_keys(settings):
    run_id, _, db, _, client = await run_graph(settings, FakeNcbi(SEARCHES, total=150))
    msgs = [r["message"] or "" for r in db.trace_events(run_id)]
    for pmid in RETRACTED:
        assert any(m.startswith(f"dedup.retracted_dropped pmid:{pmid}") for m in msgs)
    for pmid in NOTICES:
        assert any(m.startswith(f"dedup.notice_dropped pmid:{pmid}") for m in msgs)
    for pmid in ("42722381", "41911555", *sorted(NO_ABSTRACT)):
        assert f"fetch.skipped_no_abstract pmid:{pmid}" in msgs
    await client.aclose()


async def test_rerun_of_the_whole_graph_makes_zero_network_calls(settings):
    ncbi = FakeNcbi(SEARCHES, total=150)
    *_, client = await run_graph(settings, ncbi)
    await client.aclose()
    first = len(ncbi.requests)
    assert first == 9                       # per query: esearch count (Query Builder), esearch ids, efetch
    run_id, _, db, state, client = await run_graph(settings, ncbi)         # new run, same data dir
    assert len(ncbi.requests) == first                                      # the P1 acceptance criterion
    assert set(state["dropped_pmids"]) == RETRACTED | NOTICES and len(state["kept_pmids"]) == 20
    await client.aclose()


async def test_overlapping_papers_are_downloaded_once_per_run_when_fetched_one_after_another(settings):
    ncbi = FakeNcbi(SEARCHES, total=150)
    *_, client = await run_graph(settings, ncbi)
    ids = ncbi.fetched_ids()
    assert len(ids) <= 25 + 5 + 4 + 3                                       # never more than the sum of the queries
    assert set(ids) >= set(CRC_IDS) | RETRACTED | NOTICES
    await client.aclose()


async def test_a_non_dry_run_without_a_client_fails_loudly(settings):
    with pytest.raises(RuntimeError, match="needs Deps.pubmed"):
        await run_graph(settings, FakeNcbi(SEARCHES, total=150), with_client=False)


async def test_fetch_demo_reports_zero_network_calls_on_the_second_run(settings):
    from biolitsynth.pubmed.demo import fetch_demo
    ncbi = FakeNcbi()
    a = await fetch_demo("metformin AND colorectal", settings, retmax=25, transport=ncbi.transport)
    b = await fetch_demo("metformin AND colorectal", settings, retmax=25, transport=ncbi.transport)
    assert (a.candidates, a.stored, a.skipped_no_abstract, a.kept, a.network_calls) == (25, 20, 5, 20, 2)
    assert (b.network_calls, b.cache_hits, b.search_cached) == (0, 25, True)
    assert settings.paths.runs_dir.joinpath(b.run_id, "trace.jsonl").exists()
