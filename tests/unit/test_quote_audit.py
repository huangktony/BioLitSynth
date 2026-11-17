"""The independent quote audit (spec 5): audit_spans flags bad spans, quote_stats does the Gate 1 arithmetic."""
import pytest

from biolitsynth.checks.quote_audit import QuoteStats, audit_spans, quote_stats
from biolitsynth.schemas import Direction, EvidenceItem, Organism, StudyDesign

from ..fixture_papers import CASE_CONTROL, PAPERS, cut

PAPER = PAPERS[CASE_CONTROL]
QUOTE = cut(CASE_CONTROL, "No dose response", "was found.")
START = PAPER.abstract.index(QUOTE)


def item(**kw) -> EvidenceItem:
    base = dict(id=f"ev_{CASE_CONTROL}_1", pmid=CASE_CONTROL, sub_question_ids=["sq1"], statement="s", quote=QUOTE,
                quote_start=START, quote_end=START + len(QUOTE), subject="metformin", outcome="risk",
                direction=Direction.NO_EFFECT, organism=Organism.HUMAN, study_design=StudyDesign.CASE_CONTROL,
                extractor_model="m", prompt_version="extractor.v1")
    return EvidenceItem(**{**base, **kw})


def test_a_correct_span_is_not_flagged():
    assert audit_spans([item()], {CASE_CONTROL: PAPER}) == []


def test_shifted_offsets_are_flagged():
    bad = item(id="ev_32446797_2", quote_start=START + 1, quote_end=START + 1 + len(QUOTE))
    assert audit_spans([item(), bad], {CASE_CONTROL: PAPER}) == ["ev_32446797_2"]


def test_a_changed_quote_is_flagged():
    assert audit_spans([item(quote=QUOTE.replace("dose", "dosage"))], {CASE_CONTROL: PAPER}) == [f"ev_{CASE_CONTROL}_1"]


def test_missing_offsets_are_flagged():
    assert audit_spans([item(quote_start=None, quote_end=None)], {CASE_CONTROL: PAPER}) == [f"ev_{CASE_CONTROL}_1"]


def test_a_paper_that_is_not_in_the_mapping_is_flagged():
    assert audit_spans([item()], {}) == [f"ev_{CASE_CONTROL}_1"]


def test_a_quote_that_matches_its_span_but_is_too_short_or_too_long_is_flagged():
    a = PAPER.abstract
    short, long_ = a[:19], a[:601]
    assert len(short) == 19 and len(long_) == 601
    flagged = audit_spans([item(id="ev_32446797_1", quote=short, quote_start=0, quote_end=19),
                           item(id="ev_32446797_2", quote=long_, quote_start=0, quote_end=601)],
                          {CASE_CONTROL: PAPER})
    assert flagged == ["ev_32446797_1", "ev_32446797_2"]


def test_quotes_of_exactly_20_and_600_characters_pass():
    a = PAPER.abstract
    ok = [item(id="ev_32446797_1", quote=a[:20], quote_start=0, quote_end=20),
          item(id="ev_32446797_2", quote=a[:600], quote_start=0, quote_end=600)]
    assert audit_spans(ok, {CASE_CONTROL: PAPER}) == []


def row(attempt, status, seq=1, pmid="1", evidence_id=None, score=None):
    return {"run_id": "r", "pmid": pmid, "attempt": attempt, "seq": seq, "status": status, "score": score,
            "evidence_id": evidence_id}


def test_quote_stats_arithmetic():
    rows = [row(1, "exact", 1), row(1, "exact", 2), row(1, "normalized", 3), row(1, "fuzzy", 4, score=97.0),
            row(1, "not_found", 5), row(1, "too_short", 6), row(1, "too_long", 7),
            row(2, "exact", 1), row(2, "not_found", 2), row(2, "fuzzy", 3, score=96.0)]
    s = quote_stats(rows, stored=6)
    assert (s.proposed, s.first_try_ok, s.exact, s.normalized, s.fuzzy) == (7, 4, 2, 1, 1)
    assert (s.retried, s.recovered, s.dropped, s.stored) == (3, 2, 1, 6)
    assert s.first_try_rate == pytest.approx(4 / 7)


def test_attempt_two_rows_do_not_count_as_proposed_or_first_try():
    s = quote_stats([row(1, "not_found"), row(2, "exact")], stored=1)
    assert (s.proposed, s.first_try_ok, s.retried, s.recovered, s.dropped) == (1, 0, 1, 1, 0)
    assert s.first_try_rate == 0.0


def test_no_quotes_gives_a_none_rate_and_zero_counts():
    s = quote_stats([], stored=0)
    assert s.proposed == 0 and s.first_try_rate is None
    assert (s.first_try_ok, s.exact, s.normalized, s.fuzzy, s.retried, s.recovered, s.dropped, s.stored) == (0,) * 8
    assert isinstance(s, QuoteStats)


def test_all_verbatim_first_try_is_rate_one():
    assert quote_stats([row(1, "exact", i) for i in range(1, 4)], stored=3).first_try_rate == 1.0
