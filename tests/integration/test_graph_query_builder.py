"""P2 query half through the real graph: hand-given sub-questions → LLM Query Builder (scripted replies) refined
on mock esearch counts → fetch → dedup, then the run stops. Counts and PMID lists are hand-chosen, so each expected
number follows from the band (20-2,000) and the recorded fixtures."""
import json

import pytest

from biolitsynth.graph.runner import manual_plan, run_live
from biolitsynth.llm import BudgetExceeded, ModelNotVerified
from biolitsynth.pubmed import TokenBucket
from biolitsynth.store import Database

from ..llm_mock import ScriptedClient
from ..pubmed_mock import CRC_IDS, FakeNcbi

QUESTION = "Does metformin reduce colorectal cancer risk?"
SUBQ = ["metformin and colorectal cancer incidence | metformin; colorectal neoplasms",
        "metformin trials in colorectal cancer"]
DRAFT1, REFINED1 = "metformin[majr] AND colorectal neoplasms[majr] AND rct[pt]", "metformin AND colorectal neoplasms[mh]"
DRAFT2 = "metformin AND colorectal AND trial"
FAST = {"limiters": {"gemini": TokenBucket(10_000)}, "ncbi_limiter": TokenBucket(10_000), "ncbi_env": {}}


def ncbi():
    return FakeNcbi(searches={REFINED1: CRC_IDS, DRAFT2: CRC_IDS[:3]},
                    totals={DRAFT1: 4, REFINED1: 180, DRAFT2: 60})


def script():
    return ScriptedClient([
        ("(sq1): ", {"query_string": DRAFT1, "rationale": "narrow first try"}),      # draft for sq1: 4 hits
        ("Broaden it", {"query_string": REFINED1, "rationale": "dropped filters"}),  # refine for sq1: 180 hits
        ("(sq2): ", {"query_string": DRAFT2, "rationale": "in band"}),               # draft for sq2: 60 hits
    ])


async def live(settings, fake_ncbi, client):
    return await run_live(QUESTION, SUBQ, settings, clients={"query": client}, transport=fake_ncbi.transport, **FAST)


async def test_question_to_queries_to_deduped_papers(settings):
    n, client = ncbi(), script()
    lv = await live(settings, n, client)
    q = {r["id"]: r for r in lv.queries}
    assert set(q) == {"q1", "q2"}
    assert (q["q1"]["query_string"], q["q1"]["hit_count"]) == (REFINED1, 180)
    assert json.loads(q["q1"]["refinement_history_json"]) == [[DRAFT1, 4], [REFINED1, 180]]
    assert (q["q2"]["query_string"], json.loads(q["q2"]["refinement_history_json"])) == (DRAFT2, [[DRAFT2, 60]])
    # the fetch used the final query strings, never the out-of-band draft
    fetched_terms = {t for t, retmax in n.search_terms() if retmax != "0"}
    assert fetched_terms == {REFINED1, DRAFT2}
    assert lv.papers_kept == 20 and lv.papers_dropped == 0      # 25 recorded ids, 5 without abstracts
    assert lv.llm_calls == 3 and lv.llm_cache_hits == 0 and len(client.calls) == 3

    db = Database(settings.paths.data_dir / "biolitsynth.db")
    assert db.get_run(lv.run_id)["status"] == "stopped_after_dedup"
    stages = {r["stage"] for r in db.trace_events(lv.run_id)}
    assert {"plan", "query", "fetch", "dedup"} <= stages and not stages & {"rerank", "extract", "synthesize"}
    llm_spans = [r for r in db.trace_events(lv.run_id) if r["event_type"] == "llm_call"]
    assert {r["prompt_version"] for r in llm_spans} == {"query_builder.v1"} and all(r["parent_span_id"] for r in llm_spans)
    assert db.get_plan(lv.run_id).sub_questions[0].key_concepts == ["metformin", "colorectal neoplasms"]
    db.close()
    snap = json.loads((lv.run_dir / "02_queries.json").read_text())
    assert [(s["id"], s["hit_count"]) for s in snap] == [("q1", 180), ("q2", 60)]


async def test_rerun_costs_zero_llm_calls_and_zero_network_calls(settings):
    n = ncbi()
    await live(settings, n, script())
    before = len(n.requests)
    empty = ScriptedClient([])                                  # would fail if asked anything
    lv = await live(settings, n, empty)
    assert (lv.llm_calls, lv.llm_cache_hits, lv.network_calls) == (0, 3, 0) and len(n.requests) == before
    assert {r["query_string"] for r in lv.queries} == {REFINED1, DRAFT2}


async def test_run_budget_stops_the_run(settings):
    settings.budget.max_llm_calls_per_run = 2
    with pytest.raises(BudgetExceeded):
        await live(settings, ncbi(), script())
    db = Database(settings.paths.data_dir / "biolitsynth.db")
    assert [r["status"] for r in db.query("SELECT status FROM runs")] == ["failed"]
    db.close()


async def test_placeholder_model_fails_before_any_run_is_created(settings):
    settings.models.query.model = "TODO-verify-gemini-flash"
    with pytest.raises(ModelNotVerified):
        await run_live(QUESTION, SUBQ, settings, ncbi_env={})
    assert not (settings.paths.data_dir / "biolitsynth.db").exists()


@pytest.mark.parametrize("specs", [["only one"], ["a", "b", "c", "d", "e"], ["a", " | x"]])
def test_manual_plan_rejects_bad_sub_question_lists(specs):
    with pytest.raises(ValueError):
        manual_plan("r", "q", specs)


def test_manual_plan_parses_concepts_and_sets_effort():
    p = manual_plan("r", "q", ["A | x; y ;", "B", "C"])
    assert p.effort == "moderate" and [s.id for s in p.sub_questions] == ["sq1", "sq2", "sq3"]
    assert p.sub_questions[0].key_concepts == ["x", "y"] and p.sub_questions[1].key_concepts == []


async def test_without_sub_questions_the_lead_plans(settings):
    """The Lead's (scripted) plan drives the Query Builders; the plan stored is the one code built from it."""
    lead_reply = {"sub_questions": [
        {"text": "metformin and colorectal cancer incidence", "facet": "epidemiology", "rationale": "r",
         "key_concepts": ["metformin", "colorectal neoplasms"]},
        {"text": "metformin trials in colorectal cancer", "facet": "clinical_outcome", "rationale": "r",
         "key_concepts": ["metformin", "randomized controlled trial"]}],
        "bridges": [{"from_sub_question": 2, "to_sub_question": 1, "hypothesis": "h"}], "out_of_scope_notes": []}
    client = ScriptedClient([("<question>", lead_reply), *script().script])
    n = ncbi()
    lv = await run_live(QUESTION, None, settings, clients={"lead": client, "query": client},
                        transport=n.transport, **FAST)
    assert lv.plan.effort == "simple" and [s.facet.value for s in lv.plan.sub_questions] == ["epidemiology",
                                                                                              "clinical_outcome"]
    assert lv.plan.bridges[0].from_sub_question_id == "sq2"
    assert {r["query_string"] for r in lv.queries} == {REFINED1, DRAFT2} and lv.llm_calls == 4
    db = Database(settings.paths.data_dir / "biolitsynth.db")
    spans = [r for r in db.trace_events(lv.run_id) if r["event_type"] == "llm_call"]
    assert {r["prompt_version"] for r in spans} == {"lead.v1", "query_builder.v1"}
    db.close()
