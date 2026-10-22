"""Routers are pure functions, so every branch is tested without running a graph (plan §11.1)."""
from langgraph.types import Send

from biolitsynth.graph import routers as r
from biolitsynth.graph.state import merge_unique


# --- merge_unique ------------------------------------------------------------------------------
def test_merge_unique_dedupes_and_keeps_order():
    assert merge_unique(["a", "b"], ["b", "c", "a"]) == ["a", "b", "c"]
    assert merge_unique(None, ["x"]) == ["x"]
    assert merge_unique(["x"], None) == ["x"]


# --- route_after_count (query refinement loop) -----------------------------------------------
def count_route(hits, refinements, **kw):
    return r.route_after_count({"hit_count": hits, "refinements": refinements}, min_hits=20, max_hits=2000,
                               max_refinements=2, **kw)


def test_count_in_band_finalizes():
    assert [count_route(h, 0) for h in (20, 150, 2000)] == ["finalize"] * 3


def test_count_out_of_band_refines():
    assert [count_route(h, 0) for h in (0, 19, 2001, 80_000)] == ["refine"] * 4


def test_refinement_cap_stops_the_loop():
    assert count_route(0, 1) == "refine"
    assert count_route(0, 2) == "finalize"   # attempts exhausted: ship the query, trace shows the history


# --- route_after_coverage ----------------------------------------------------------------------
def test_coverage_gap_launches_round():
    s = {"gap_sub_question_ids": ["sq2"], "coverage_rounds": 1}
    assert r.route_after_coverage(s, max_extra_rounds=1) == "dispatch_queries"


def test_coverage_no_gap_moves_on():
    assert r.route_after_coverage({"gap_sub_question_ids": [], "coverage_rounds": 0}, max_extra_rounds=1) == "cluster_studies"


def test_coverage_round_cap_is_a_backstop():
    s = {"gap_sub_question_ids": ["sq2"], "coverage_rounds": 2}
    assert r.route_after_coverage(s, max_extra_rounds=1) == "cluster_studies"


# --- route_after_checks ------------------------------------------------------------------------
def test_checks_clean_goes_to_critic():
    assert r.route_after_checks({"claim_ids": ["cl1", "cl2"], "hard_failed_claim_ids": []}) == "critique"


def test_checks_some_hard_failed_still_critiques_the_rest():
    assert r.route_after_checks({"claim_ids": ["cl1", "cl2"], "hard_failed_claim_ids": ["cl1"]}) == "critique"


def test_checks_all_hard_failed_skips_critic():
    assert r.route_after_checks({"claim_ids": ["cl1"], "hard_failed_claim_ids": ["cl1"]}) == "route"


def test_checks_no_claims_skips_critic():
    assert r.route_after_checks({"claim_ids": []}) == "route"


# --- route_after_critic: attempt bookkeeping ---------------------------------------------------
def critic_route(attempt, failed=("cl1",)):
    return r.route_after_critic({"failed_claim_ids": list(failed), "synth_attempt": attempt}, max_retries=2)


def test_nothing_failed_assembles():
    assert critic_route(0, failed=()) == "assemble"


def test_exactly_two_retries_then_assemble():
    # attempt 0 = first synthesis. 0 and 1 may retry; 2 may not: three syntheses, two of them retries.
    assert [critic_route(a) for a in (0, 1, 2, 3)] == ["synthesize", "synthesize", "assemble", "assemble"]


def test_zero_retries_configured():
    assert r.route_after_critic({"failed_claim_ids": ["cl1"], "synth_attempt": 0}, max_retries=0) == "assemble"


# --- fan-out -----------------------------------------------------------------------------------
def test_fan_out_queries_one_per_sub_question():
    sends = r.fan_out_queries({"sub_question_ids": ["sq1", "sq2", "sq3"], "query_ids": [], "dispatch_span_id": "sp"})
    assert all(isinstance(s, Send) and s.node == "query_builder" for s in sends)
    assert [s.arg["query_id"] for s in sends] == ["q1", "q2", "q3"]
    assert [s.arg["sub_question_id"] for s in sends] == ["sq1", "sq2", "sq3"]
    assert sends[0].arg["parent_span_id"] == "sp"


def test_gap_round_targets_only_gaps_and_never_reuses_query_ids():
    sends = r.fan_out_queries({"sub_question_ids": ["sq1", "sq2", "sq3"], "gap_sub_question_ids": ["sq2"],
                               "query_ids": ["q1", "q2", "q3"], "coverage_rounds": 1})
    assert [(s.arg["sub_question_id"], s.arg["query_id"], s.arg["round"]) for s in sends] == [("sq2", "q4", 1)]


def test_fan_out_with_nothing_to_do_still_goes_somewhere():
    # A conditional edge that returns [] would end the run silently.
    assert r.fan_out_queries({}) == "approve_queries"
    assert r.fan_out_fetch({"query_ids": ["q1"], "fetched_query_ids": ["q1"]}) == "dedup"
    assert r.fan_out_extract({"kept_pmids": ["1"], "extracted_pmids": ["1"]}, batch_size=5) == "quote_check"


def test_fetch_skips_already_fetched_queries():
    sends = r.fan_out_fetch({"query_ids": ["q1", "q2", "q3"], "fetched_query_ids": ["q1", "q2"]})
    assert [s.arg["query_id"] for s in sends] == ["q3"]


def test_extract_batches_and_skips_already_extracted():
    pmids = [str(i) for i in range(12)]
    sends = r.fan_out_extract({"kept_pmids": pmids, "extracted_pmids": []}, batch_size=5)
    assert [len(s.arg["pmids"]) for s in sends] == [5, 5, 2]
    sends = r.fan_out_extract({"kept_pmids": pmids, "extracted_pmids": pmids[:10]}, batch_size=5)
    assert [s.arg["pmids"] for s in sends] == [["10", "11"]]
