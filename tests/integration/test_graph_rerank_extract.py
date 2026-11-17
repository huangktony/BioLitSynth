"""P3 through the real graph: question -> queries -> fetch -> dedup -> rerank (FakeReranker) -> Extractor (scripted
LLM) -> quote audit, then the run stops. No network, no model.

Hand-worked setup (CRC_IDS are the 25 recorded ids; five of them have no abstract and are never stored):
  papers_per_query = 3. Scores: A 9, B 8, C 7, D 6, E 5, everything else 0.0 (ties fall back to PubMed order).
  A = 32446797 (case-control), B = 32592092 (meta-analysis), C = 32968891 (cohort, 323,430 people),
  D = 27496094 (veterans cohort, 21,352 patients), E = 26331456 (cohort, 1,197 patients).
  sq1 query -> all 25 ids: 20 candidates (the 5 without abstracts are not stored). Top 3 by score: A, B, C.
  sq2 query -> [D, A, E, 36645225, 35979933, 41911553]. 41911553 is a retracted RCT that dedup drops, so 5
  candidates. Top 3: A (9), D (6), E (5).
  kept_pmids = sq1 by rank [A, B, C] then sq2 by rank [A, D, E], first occurrence kept = [A, B, C, D, E];
  one extraction batch (5 abstracts per batch), id r0b1.
  A is kept for both sub-questions.
Scripted extractor reply, in one batch:
  A: two verbatim findings (exact).             B: one finding, quote typed with plain spaces (normalized).
  C: one finding with one typo in the quote (fuzzy).
  D: a made-up quote; the retry gives the real sentence (recovered).   E: made-up quote, made-up again (dropped).
  plus a finding for a pmid that is not in the batch.
Expected quotes: 6 proposed on the first try, 4 verbatim (A, A, B, C); 5 items stored; strength from spec 4.1:
  A human case-control, n unknown -> low;  B meta-analysis -> high;  C human cohort n=323430 -> moderate;
  D human cohort n=21352 -> moderate.
"""
import json
from pathlib import Path

import pytest

from biolitsynth.agents import extractor
from biolitsynth.checks.quote_audit import QuoteAuditError, audit_spans
from biolitsynth.graph.runner import run_live
from biolitsynth.pubmed import TokenBucket
from biolitsynth.rerank import FakeReranker, query_text
from biolitsynth.schemas import EvidenceItem
from biolitsynth.store import Database

from ..fixture_papers import CASE_CONTROL as A, COHORT as C, META as B, OTHER as E, VETERANS as D, cut
from ..llm_mock import ScriptedClient
from ..pubmed_mock import CRC_IDS, FakeNcbi

QUESTION = "Does metformin reduce colorectal cancer risk?"
SUBQ = ["metformin and colorectal cancer incidence | metformin; colorectal neoplasms",
        "metformin and colorectal cancer survival"]
Q1, Q2 = "metformin AND colorectal neoplasms[mh]", "metformin AND survival"
NO_ABSTRACT = {"28506970", "30385499", "32384400", "28506966", "21949227"}
RETRACTED = "41911553"
SEARCHES = {Q1: CRC_IDS, Q2: [D, A, E, "36645225", "35979933", RETRACTED]}
FAST = {"limiters": {"gemini": TokenBucket(10_000)}, "ncbi_limiter": TokenBucket(10_000), "ncbi_env": {}}
SCORES = {A: 9.0, B: 8.0, C: 7.0, D: 6.0, E: 5.0}

MADE_UP_D = "Metformin eliminated all colorectal tumours within one week of dosing."
MADE_UP_E = "Every participant on metformin was cured of colorectal cancer by the end."
MADE_UP_E2 = "Metformin doubled survival in every colorectal cancer patient studied."
Q_A1 = cut(A, "Metformin use prior", "0.004).")
Q_A2 = cut(A, "No dose response", "was found.")
Q_B = cut(B, "Overall survival (HR", "non-metformin users.")
Q_C = cut(C, "Metformin users with diabetes appear", "non-users.")
Q_D = cut(D, "Among colorectal cancer patients with diabetes, metformin", "P = 0.003)")
Q_B_TYPED = Q_B.replace(" ", " ")                 # normalized: the abstract has thin spaces
Q_C_TYPED = Q_C.replace("significantly", "significanty")   # fuzzy: one typo


def finding(pmid, quote, statement, design="cohort", **kw):
    return {"pmid": pmid, "statement": statement, "quote": quote, "subject": "Metformin", "outcome": "CRC",
            "direction": "decreases", "organism": "human", "study_design": design, **kw}


def query_client():
    return ScriptedClient([("(sq1): ", {"query_string": Q1, "rationale": "r"}),
                           ("(sq2): ", {"query_string": Q2, "rationale": "r"})])


def extractor_client():
    return ScriptedClient([
        (MADE_UP_D, {"findings": [finding(D, Q_D, "d retried", sample_size=21352)]}),          # retry replies first
        (MADE_UP_E, {"findings": [finding(E, MADE_UP_E2, "e retried again")]}),
        (f'pmid="{A}"', {"findings": [
            finding(A, Q_A1, "a1", design="case_control"), finding(A, Q_A2, "a2", design="case_control"),
            finding(B, Q_B_TYPED, "b1", design="cohort"),
            finding(C, Q_C_TYPED, "c1", sample_size=323430),
            finding(D, MADE_UP_D, "d1", sample_size=21352),
            finding(E, MADE_UP_E, "e1"),
            finding("99999999", Q_A2, "stray", design="case_control")]}),
    ])


def setup(settings, top_x=3):
    settings.limits.papers_per_query = top_x
    settings.rerank.min_score = None


async def live(settings, ncbi, *, reranker=None, qc=None, ec=None):
    return await run_live(QUESTION, SUBQ, settings, clients={"query": qc or query_client(),
                                                              "extractor": ec or extractor_client()},
                          reranker=reranker or FakeReranker(SCORES), transport=ncbi.transport, **FAST), ncbi


def ncbi():
    return FakeNcbi(SEARCHES, total=150)


def rows(db, sql, *args):
    return [dict(r) for r in db.query(sql, args)]


@pytest.fixture
async def first_run(settings):
    setup(settings)
    lv, n = await live(settings, ncbi())
    db = Database(settings.paths.data_dir / "biolitsynth.db")
    yield settings, lv, db, n
    db.close()


async def test_the_run_goes_past_dedup_and_stops_after_the_quote_check(first_run):
    settings, lv, db, _ = first_run
    assert db.get_run(lv.run_id)["status"] == "stopped_after_quote_check"
    assert lv.papers_kept == 20                                        # dedup numbers are unchanged by P3
    assert lv.papers_dropped == 1
    stages = {r["stage"] for r in db.trace_events(lv.run_id)}
    assert {"rerank", "extract"} <= stages and "synthesize" not in stages
    assert (lv.run_dir / "04_evidence.json").exists()


async def test_per_sub_question_top_x_with_a_score_row_for_every_candidate(first_run):
    _, lv, db, _ = first_run
    sq1 = rows(db, "SELECT * FROM relevance_scores WHERE run_id=? AND sub_question_id='sq1' ORDER BY rank", lv.run_id)
    sq2 = rows(db, "SELECT * FROM relevance_scores WHERE run_id=? AND sub_question_id='sq2' ORDER BY rank", lv.run_id)
    assert len(sq1) == 20 and len(sq2) == 5
    assert {r["pmid"] for r in sq1}.isdisjoint(NO_ABSTRACT)
    assert RETRACTED not in {r["pmid"] for r in sq2}                  # dropped by dedup, never a candidate
    assert [r["pmid"] for r in sq1[:3]] == [A, B, C] and [r["pmid"] for r in sq2[:3]] == [A, D, E]
    assert [r["score"] for r in sq1[:3]] == [9.0, 8.0, 7.0] and [r["rank"] for r in sq1] == list(range(1, 21))
    for group in (sq1, sq2):
        assert [bool(r["kept"]) for r in group] == [True] * 3 + [False] * (len(group) - 3)
        assert [r["drop_reason"] for r in group] == [None] * 3 + ["below_top_x"] * (len(group) - 3)
        assert {r["model"] for r in group} == {"fake-reranker"}
    sq2_by_pmid = {r["pmid"]: r for r in sq2}
    assert [sq2_by_pmid[p]["pubmed_rank"] for p in (D, A, E)] == [1, 2, 3]       # position in the sq2 esearch result
    plan = lv.plan.sub_questions
    import hashlib
    assert {r["query_text_sha256"] for r in sq1} == {hashlib.sha256(query_text(plan[0]).encode()).hexdigest()}
    assert {r["query_text_sha256"] for r in sq2} == {hashlib.sha256(query_text(plan[1]).encode()).hexdigest()}
    assert lv.rerank_by_sub_question == {"sq1": (20, 3), "sq2": (5, 3)} and lv.rerank_model == "fake-reranker"


async def test_kept_pmids_are_the_ordered_union_and_a_shared_paper_serves_both_sub_questions(first_run):
    _, lv, db, _ = first_run
    assert lv.state["kept_pmids"] == [A, B, C, D, E]
    sq = rows(db, "SELECT evidence_id, sub_question_id FROM evidence_subq WHERE run_id=? ORDER BY 1, 2", lv.run_id)
    by_ev: dict[str, list[str]] = {}
    for r in sq:
        by_ev.setdefault(r["evidence_id"], []).append(r["sub_question_id"])
    assert by_ev[f"ev_{A}_1"] == ["sq1", "sq2"] and by_ev[f"ev_{A}_2"] == ["sq1", "sq2"]
    assert by_ev[f"ev_{B}_1"] == ["sq1"] and by_ev[f"ev_{C}_1"] == ["sq1"]
    assert by_ev[f"ev_{D}_1"] == ["sq2"]


async def test_evidence_rows_are_the_verified_findings_with_code_set_fields(first_run):
    _, lv, db, _ = first_run
    ev = {r["id"]: r for r in rows(db, "SELECT * FROM evidence WHERE run_id=?", lv.run_id)}
    assert sorted(ev) == sorted([f"ev_{A}_1", f"ev_{A}_2", f"ev_{B}_1", f"ev_{C}_1", f"ev_{D}_1"])
    assert not [k for k in ev if k.startswith(f"ev_{E}")]               # E's quote never verified
    assert ev[f"ev_{A}_1"]["quote"] == Q_A1 and ev[f"ev_{A}_2"]["quote"] == Q_A2
    assert ev[f"ev_{B}_1"]["quote"] == Q_B                               # the abstract's text, not the typed one
    assert ev[f"ev_{C}_1"]["quote"].startswith("Metformin users with diabetes appear")
    assert ev[f"ev_{D}_1"]["quote"] == Q_D
    assert ev[f"ev_{A}_1"]["subject"] == "metformin" and ev[f"ev_{A}_1"]["outcome"] == "crc"
    assert {k: ev[k]["strength"] for k in ev} == {f"ev_{A}_1": "low", f"ev_{A}_2": "low", f"ev_{B}_1": "high",
                                                  f"ev_{C}_1": "moderate", f"ev_{D}_1": "moderate"}
    assert ev[f"ev_{B}_1"]["study_design"] == "meta_analysis"            # PubMed's publication type wins
    assert (ev[f"ev_{C}_1"]["sample_size"], ev[f"ev_{D}_1"]["sample_size"]) == (323430, 21352)
    assert ev[f"ev_{A}_1"]["sample_size"] is None


async def test_every_stored_evidence_row_passes_the_independent_span_audit(first_run):
    _, lv, db, _ = first_run
    items = load_items(db, lv.run_id)
    assert len(items) == 5
    papers = db.get_papers(sorted({i.pmid for i in items}))
    assert audit_spans(items, papers) == []
    snap = json.loads((lv.run_dir / "04_evidence.json").read_text())
    assert sorted(s["id"] for s in snap) == sorted(i.id for i in items)


def load_items(db, run_id):
    out = []
    for r in rows(db, "SELECT * FROM evidence WHERE run_id=?", run_id):
        sq = [x["sub_question_id"] for x in rows(db, "SELECT sub_question_id FROM evidence_subq WHERE run_id=? "
                                                     "AND evidence_id=? ORDER BY 1", run_id, r["id"])]
        out.append(EvidenceItem(
            **{k: r[k] for k in ("id", "pmid", "statement", "quote", "quote_start", "quote_end", "subject", "outcome",
                                 "direction", "effect_size", "population", "organism", "study_design", "sample_size",
                                 "cohort_or_trial_name", "strength")},
            sub_question_ids=sq, extractor_model="m", prompt_version="p"))
    return out


async def test_quote_log_has_one_row_per_proposed_quote_kept_or_not(first_run):
    _, lv, db, _ = first_run
    log = {(r["pmid"], r["attempt"], r["seq"]): r for r in rows(db, "SELECT * FROM quote_log WHERE run_id=?", lv.run_id)}
    assert {k: (v["status"], v["evidence_id"]) for k, v in log.items()} == {
        (A, 1, 1): ("exact", f"ev_{A}_1"), (A, 1, 2): ("exact", f"ev_{A}_2"),
        (B, 1, 1): ("normalized", f"ev_{B}_1"), (C, 1, 1): ("fuzzy", f"ev_{C}_1"),
        (D, 1, 1): ("not_found", None), (D, 2, 1): ("exact", f"ev_{D}_1"),
        (E, 1, 1): ("not_found", None), (E, 2, 1): ("not_found", None)}
    assert log[(C, 1, 1)]["score"] >= 95 and log[(A, 1, 1)]["score"] is None
    q = lv.quotes
    assert (q.proposed, q.first_try_ok, q.exact, q.normalized, q.fuzzy) == (6, 4, 2, 1, 1)
    assert (q.retried, q.recovered, q.dropped, q.stored) == (2, 1, 1, 5)
    assert q.first_try_rate == pytest.approx(4 / 6)
    assert (lv.papers_extracted, lv.evidence_items) == (5, 5)


async def test_trace_has_a_span_per_sub_question_and_the_extractor_validation_events(first_run):
    _, lv, db, _ = first_run
    ev = db.trace_events(lv.run_id)
    rerank = [e for e in ev if e["stage"] == "rerank" and e["agent"] == "reranker"]
    msgs = [e["message"] for e in rerank]
    assert "sq1: 20 candidates → 3 kept" in msgs and "sq2: 5 candidates → 3 kept" in msgs
    sq_spans = [e for e in rerank if (e["message"] or "").startswith("sq")]
    assert {e["parent_span_id"] for e in sq_spans} == {lv.state["dispatch_span_id"]}
    sq1_span = next(e for e in sq_spans if e["message"].startswith("sq1"))
    assert json.loads(sq1_span["output_refs_json"]) == [f"pmid:{p}" for p in (A, B, C)]
    batch = next(e for e in ev if (e["message"] or "").startswith("batch r0b1"))
    assert batch["message"] == "batch r0b1: 5 abstracts → 5 items (2 retried, 1 dropped)"   # 2 papers retried, 1 finding dropped
    assert batch["parent_span_id"] == lv.state["dispatch_span_id"]
    val = [e["message"] for e in ev if e["agent"] == "quote_span_validator" and e["event_type"] == "validation"]
    assert any(m.startswith(f"validation.quote_snapped ev_{C}_1 score=") for m in val)
    assert f"validation.quote_dropped pmid:{E} n=1" in val
    assert "extract.unknown_pmid 99999999" in val
    retries = [e["message"] for e in ev if e["event_type"] == "retry" and (e["message"] or "").startswith("validation.")]
    assert sorted(retries) == sorted([f"validation.quote_retry pmid:{D}", f"validation.quote_retry pmid:{E}"])
    last = [e for e in ev if e["agent"] == "quote_span_validator" and "items stored" in (e["message"] or "")]
    assert last and "5 items stored" in last[0]["message"]


async def test_llm_calls_are_two_query_builders_one_batch_and_two_retries(first_run):
    _, lv, db, _ = first_run
    assert lv.llm_calls == 5 and lv.llm_cache_hits == 0


async def test_a_second_identical_run_makes_no_llm_calls_and_no_reranker_calls(settings):
    setup(settings)
    n = ncbi()
    first, _ = await live(settings, n)
    before = len(n.requests)
    rr = FakeReranker(SCORES)
    second, _ = await live(settings, n, reranker=rr, qc=ScriptedClient([]), ec=ScriptedClient([]))
    assert (second.llm_calls, second.llm_cache_hits) == (0, 5) and rr.calls == 0 and len(n.requests) == before
    assert second.state["kept_pmids"] == first.state["kept_pmids"] == [A, B, C, D, E]
    db = Database(settings.paths.data_dir / "biolitsynth.db")
    ids = lambda run: sorted(r["id"] for r in db.query("SELECT id FROM evidence WHERE run_id=?", (run,)))  # noqa: E731
    assert ids(second.run_id) == ids(first.run_id) and len(ids(first.run_id)) == 5
    db.close()


# --- fallback (plan 5.4c) ---------------------------------------------------------------------------
class BrokenReranker:
    model = "broken"

    def __init__(self):
        self.calls = 0

    def score(self, query, docs):
        self.calls += 1
        raise RuntimeError("model could not load")


async def test_a_failing_reranker_falls_back_to_pubmed_order_and_the_run_completes(settings):
    setup(settings)
    broken = BrokenReranker()
    ec = ScriptedClient([("", {"findings": []})] * 2)
    lv, _ = await live(settings, ncbi(), reranker=broken, ec=ec)
    db = Database(settings.paths.data_dir / "biolitsynth.db")
    assert db.get_run(lv.run_id)["status"] == "stopped_after_quote_check"
    assert broken.calls == 1                                            # not retried for sq2
    plain = [i for i in CRC_IDS if i not in NO_ABSTRACT]
    r1 = rows(db, "SELECT * FROM relevance_scores WHERE run_id=? AND sub_question_id='sq1' ORDER BY rank", lv.run_id)
    r2 = rows(db, "SELECT * FROM relevance_scores WHERE run_id=? AND sub_question_id='sq2' ORDER BY rank", lv.run_id)
    assert [r["pmid"] for r in r1] == plain and [r["rank"] for r in r1] == list(range(1, 21))
    assert [r["pmid"] for r in r2] == [D, A, E, "36645225", "35979933"]
    for group in (r1, r2):
        assert {r["model"] for r in group} == {"fallback:pubmed_order"} and {r["score"] for r in group} == {0.0}
        assert [bool(r["kept"]) for r in group] == [True] * 3 + [False] * (len(group) - 3)
        assert {r["drop_reason"] for r in group[3:]} == {"below_top_x"}
    assert lv.rerank_model == "fallback:pubmed_order"
    assert lv.state["kept_pmids"] == plain[:3] + [p for p in [D, A, E] if p not in plain[:3]]
    errs = [e for e in db.trace_events(lv.run_id) if e["event_type"] == "error" and e["stage"] == "rerank"]
    assert len(errs) == 1 and errs[0]["agent"] == "reranker"
    assert errs[0]["message"].startswith("rerank.fallback ") and "RuntimeError" in errs[0]["message"]
    assert "model could not load" in errs[0]["message"]
    db.close()


async def test_a_non_dry_run_without_a_reranker_in_deps_fails_loudly(settings):
    from langgraph.checkpoint.memory import InMemorySaver
    from biolitsynth.graph import Deps, build_graph, initial_state
    from biolitsynth.graph.runner import make_stage_llms, manual_plan, new_run_id
    from biolitsynth.pubmed import PubMedClient
    from biolitsynth.trace import Tracer
    setup(settings)
    n, run_id = ncbi(), new_run_id()
    db = Database(settings.paths.data_dir / "biolitsynth.db")
    tr = Tracer(db, run_id)
    db.create_run(run_id, "q?", settings)
    client = PubMedClient(settings.ncbi, db, settings.paths.data_dir / "cache" / "pubmed", tracer=tr,
                          transport=n.transport, env={}, limiter=TokenBucket(10_000))
    llms = make_stage_llms(settings, db, tr, roles=("query", "extractor"),
                           clients={"query": query_client(), "extractor": extractor_client()},
                           limiters={"gemini": TokenBucket(10_000)})
    deps = Deps(settings=settings, db=db, tracer=tr, run_dir=settings.paths.runs_dir / run_id, pubmed=client,
                llms=llms, manual_plan=manual_plan(run_id, "q?", SUBQ))
    graph = build_graph(deps, InMemorySaver())
    with pytest.raises(RuntimeError, match="reranker"):
        await graph.ainvoke(initial_state(run_id, "q?", dry=False),
                            {"recursion_limit": 60, "configurable": {"thread_id": run_id}})
    await client.aclose()
    db.close()


# --- the audit is independent of the extractor -------------------------------------------------------
async def test_a_stored_item_with_a_bad_span_makes_the_audit_raise(settings, monkeypatch):
    setup(settings)
    real = extractor.check_findings

    def corrupt(*a, **k):
        items, failed, outcomes, warnings = real(*a, **k)
        return [i.model_copy(update={"quote_start": i.quote_start + 1}) for i in items], failed, outcomes, warnings

    monkeypatch.setattr(extractor, "check_findings", corrupt)
    with pytest.raises(QuoteAuditError, match=f"ev_{A}_1"):
        await live(settings, ncbi())
    db = Database(settings.paths.data_dir / "biolitsynth.db")
    assert [r["status"] for r in db.query("SELECT status FROM runs")] == ["failed"]
    db.close()
