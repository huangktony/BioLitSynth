import pytest
from pydantic import ValidationError

from biolitsynth.schemas import (Claim, ClaimType, Conflict, EvidenceItem, Paper, PubMedQuery, ResearchPlan,
                                 StudyCluster, SubQuestion, TraceEvent, Verdict)


def claim(**kw):
    base = dict(id="cl1", text="t", claim_type=ClaimType.DIRECT, sub_question_ids=["sq1"],
                evidence_ids=["ev_1_1"], confidence="low")
    return Claim(**{**base, **kw})


# --- Claim type rules (plan §4.5, §11.1: one test per rule) ----------------------------------
def test_direct_with_evidence_ok():
    claim()


def test_non_gap_claim_needs_evidence():
    with pytest.raises(ValidationError, match="need evidence_ids"):
        claim(evidence_ids=[])


def test_gap_claim_needs_no_evidence():
    claim(claim_type=ClaimType.GAP, evidence_ids=[])


def test_inferred_needs_two_evidence_ids():
    step = {"premise_evidence_ids": ["ev_1_1"], "inference": "x"}
    with pytest.raises(ValidationError, match="≥2 evidence ids"):
        claim(claim_type=ClaimType.INFERRED, evidence_ids=["ev_1_1"], reasoning=[step])


def test_inferred_needs_reasoning():
    with pytest.raises(ValidationError, match="reasoning steps"):
        claim(claim_type=ClaimType.INFERRED, evidence_ids=["ev_1_1", "ev_2_1"])


def test_inferred_ok():
    step = {"premise_evidence_ids": ["ev_1_1", "ev_2_1"], "inference": "x"}
    claim(claim_type=ClaimType.INFERRED, evidence_ids=["ev_1_1", "ev_2_1"], reasoning=[step])


def test_contested_needs_conflict_id():
    with pytest.raises(ValidationError, match="reference a conflict"):
        claim(claim_type=ClaimType.CONTESTED)
    claim(claim_type=ClaimType.CONTESTED, conflict_id="cf1")


# --- ids and bounds ----------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["cl_04", "c1", "cl", "CL1"])
def test_claim_id_pattern(bad):
    # Plan prose says "cl_04" but the schema says ^cl\d+$. The schema wins (docs/OPEN_QUESTIONS.md).
    with pytest.raises(ValidationError):
        claim(id=bad)


@pytest.mark.parametrize("n,ok", [(1, False), (2, True), (4, True), (5, False)])
def test_plan_sub_question_bounds(n, ok):
    sqs = [SubQuestion(id=f"sq{i}", text="t", facet="other", rationale="r", key_concepts=[]) for i in range(n)]
    build = lambda: ResearchPlan(run_id="r", question="q", effort="moderate", sub_questions=sqs)
    if ok:
        build()
    else:
        with pytest.raises(ValidationError):
            build()


def test_paper_pmid_pattern():
    kw = dict(title="t", abstract="a", abstract_sha256="h", authors=[], publication_types=[], mesh_terms=[],
              retrieved_by=[], rank_by_query={})
    Paper(pmid="31415926", **kw)
    for bad in ("", "abc", "1234567890"):
        with pytest.raises(ValidationError):
            Paper(pmid=bad, **kw)


def test_evidence_id_pattern():
    kw = dict(pmid="1", sub_question_ids=[], statement="s", quote="q", subject="a", outcome="b",
              direction="increases", organism="human", study_design="rct", extractor_model="m", prompt_version="v1")
    EvidenceItem(id="ev_31415926_2", **kw)
    with pytest.raises(ValidationError):
        EvidenceItem(id="ev_x_2", **kw)


def test_study_cluster_needs_a_pmid():
    with pytest.raises(ValidationError):
        StudyCluster(id="st1", pmids=[], primary_pmid="1", confidence="certain")


def test_conflict_needs_two_sides():
    with pytest.raises(ValidationError):
        Conflict(id="cf1", canonical_subject="a", canonical_outcome="b", sides=[], conflict_type="direction",
                 balance="balanced", status="unresolved", adjudicator_model="m")


def test_enums_reject_unknown_values():
    with pytest.raises(ValidationError):
        Verdict(claim_id="cl1", per_evidence=[], overall_support="maybe", label_correct=True,
                status="pass", reasons=[], critic_model="m", attempt=0)


def test_pubmed_query_defaults_match_limits_table():
    q = PubMedQuery(id="q1", sub_question_id="sq1", query_string="x")
    assert (q.retmax_candidates, q.keep_top) == (100, 25)


def test_trace_event_stage_is_closed_set():
    kw = dict(run_id="r", span_id="s", parent_span_id=None, agent="a", event_type="decision", status="ok",
              ts="2026-10-22T00:00:00Z")
    TraceEvent(stage="route", **kw)
    with pytest.raises(ValidationError):
        TraceEvent(stage="not_a_stage", **kw)


def test_models_roundtrip_through_json():
    c = claim()
    assert Claim.model_validate_json(c.model_dump_json()) == c
