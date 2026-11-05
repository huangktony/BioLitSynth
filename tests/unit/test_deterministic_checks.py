"""Deterministic checks (plan §5.9) and the four W1 injections from §11.4.

The baseline report is built from recorded fixture papers; its quotes are sentences cut from those abstracts and
located by the quote validator. Each injection mutates the baseline the way §11.4 describes, and the expected catch
is the one the plan's table names, not something chosen to fit the code.
"""
import re
from pathlib import Path

import pytest

from biolitsynth.checks.deterministic import HARD_FAIL, RunFacts, failures, run_checks
from biolitsynth.checks.quote_span import apply_span
from biolitsynth.pubmed import parse_pubmed_xml
from biolitsynth.schemas import (Citation, CitedReport, CitedSentence, Claim, ClaimType, Conflict, Direction,
                                 EvidenceItem, Organism, SideSummary, StudyDesign)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "pubmed"
PAPERS = {p.pmid: p for f in sorted(FIX.glob("*.xml")) for p in parse_pubmed_xml(f.read_bytes()).papers}
GOOD = [p for p in PAPERS.values() if not p.is_retracted][:3]
RETRACTED = next(p for p in PAPERS.values() if p.is_retracted)


def evidence(paper, n, direction):
    sentence = re.search(r"[^\s.][^.\n]{39,}?[.]", paper.abstract).group(0)
    item = EvidenceItem(id=f"ev_{paper.pmid}_{n}", pmid=paper.pmid, sub_question_ids=["sq1"], statement="s",
                        quote=sentence, subject="metformin", outcome="crc", direction=direction,
                        organism=Organism.HUMAN, study_design=StudyDesign.COHORT, extractor_model="m",
                        prompt_version="extractor.v1")
    placed, _ = apply_span(item, paper.abstract)
    return placed


def baseline():
    e1, e2, e3 = (evidence(GOOD[0], 1, Direction.DECREASES), evidence(GOOD[1], 1, Direction.NO_EFFECT),
                  evidence(GOOD[2], 1, Direction.DECREASES))
    ev = {e.id: e for e in (e1, e2, e3)}
    side = lambda pos, items: SideSummary(position=pos, evidence_ids=[i.id for i in items],
                                          study_ids=[f"st{i.pmid}" for i in items], weight=2.0,
                                          best_design=StudyDesign.COHORT, organisms=[Organism.HUMAN])
    cf = Conflict(id="cf1", canonical_subject="metformin", canonical_outcome="crc",
                  sides=[side(Direction.DECREASES, [e1, e3]), side(Direction.NO_EFFECT, [e2])],
                  conflict_type="direction", balance="balanced", status="unresolved", adjudicator_model="m")
    claims = [
        Claim(id="cl1", text="direct", claim_type=ClaimType.DIRECT, sub_question_ids=["sq1"], evidence_ids=[e1.id],
              confidence="moderate"),
        Claim(id="cl2", text="contested", claim_type=ClaimType.CONTESTED, sub_question_ids=["sq1"],
              evidence_ids=[e1.id, e2.id], conflict_id="cf1", confidence="low"),
    ]
    cite = lambda e: Citation(evidence_id=e.id, pmid=e.pmid, quote=e.quote)
    report = CitedReport(run_id="r1", question="q", claims=claims, uncited_sentences=[], sections={"sq1": [
        CitedSentence(text="A [cl1].", claim_ids=["cl1"], citations=[cite(e1)]),
        CitedSentence(text="B [cl2].", claim_ids=["cl2"], citations=[cite(e1), cite(e2)])]})
    facts = RunFacts(evidence=ev, papers={p.pmid: p for p in (*GOOD, RETRACTED)}, conflicts={"cf1": cf})
    return report, facts


def failed_checks(report, facts):
    return {(r.check, r.claim_id) for r in failures(run_checks(report, facts))}


def test_the_baseline_passes_every_check():
    report, facts = baseline()
    results = run_checks(report, facts)
    assert results and not failures(results)
    assert {r.check for r in results} == {"evidence_exists", "claim_type_rules", "retraction_guard",
                                          "contested_both_sides", "pmid_in_run", "quote_in_abstract",
                                          "no_orphan_sentences"}


# ---- §11.4 injections (the four deterministic ones scheduled for W1) -------------------------------------------
def test_injection_nonexistent_evidence_id_is_a_hard_fail():
    report, facts = baseline()
    report.claims[0].evidence_ids = ["ev_99999999_1"]
    assert ("evidence_exists", "cl1") in failed_checks(report, facts) and "evidence_exists" in HARD_FAIL


def test_injection_real_pmid_not_retrieved_this_run_is_a_hard_fail():
    report, facts = baseline()
    report.sections["sq1"][0].citations.append(Citation(evidence_id="ev_x_1", pmid="31415926", quote="q"))
    assert ("pmid_in_run", "cl1") in failed_checks(report, facts) and "pmid_in_run" in HARD_FAIL


def test_injection_sentence_with_no_claim_marker_is_a_hard_fail():
    report, facts = baseline()
    report.uncited_sentences.append("Metformin cures cancer.")
    assert ("no_orphan_sentences", "*") in failed_checks(report, facts) and "no_orphan_sentences" in HARD_FAIL


def test_injection_contested_claim_missing_one_side_fails():
    report, facts = baseline()
    report.claims[1].evidence_ids = [report.claims[0].evidence_ids[0]]      # only the "decreases" side
    assert failed_checks(report, facts) == {("contested_both_sides", "cl2")}


# ---- the other schema-only checks -------------------------------------------------------------------------------
def test_quote_moved_off_its_offsets_or_edited_in_a_citation_fails():
    report, facts = baseline()
    e = facts.evidence[report.claims[0].evidence_ids[0]]
    facts.evidence[e.id] = e.model_copy(update={"quote_start": e.quote_start + 1})
    assert ("quote_in_abstract", "cl1") in failed_checks(report, facts)
    report, facts = baseline()
    report.sections["sq1"][0].citations[0].quote += " and more"
    assert ("quote_in_abstract", "cl1") in failed_checks(report, facts)


def test_citing_a_retracted_paper_is_a_hard_fail():
    report, facts = baseline()
    bad = evidence(RETRACTED, 1, Direction.DECREASES)
    facts.evidence[bad.id] = bad
    report.claims[0].evidence_ids.append(bad.id)
    assert ("retraction_guard", "cl1") in failed_checks(report, facts)


def test_claim_that_bypassed_its_validators_is_caught():
    report, facts = baseline()
    report.claims.append(Claim.model_construct(id="cl3", text="t", claim_type=ClaimType.INFERRED,
                                               sub_question_ids=["sq1"], evidence_ids=["ev_" + GOOD[0].pmid + "_1"],
                                               reasoning=[], confidence="low", caveats=[], conflict_id=None))
    assert ("claim_type_rules", "cl3") in failed_checks(report, facts)


def test_contested_claim_pointing_at_an_unknown_conflict_fails():
    report, facts = baseline()
    report.claims[1].conflict_id = "cf9"
    assert ("contested_both_sides", "cl2") in failed_checks(report, facts)
