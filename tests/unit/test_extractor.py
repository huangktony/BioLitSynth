"""Extractor, part 1: check_findings (code decides what survives) and extract_batch (one call + one retry).

Expected quotes are sliced out of real fixture abstracts with `cut`. LLM replies are scripted inputs. Warning texts
and strength values come from the P3 spec (sections 4.1 to 4.3).
"""
import asyncio
import json

import pytest

from biolitsynth.agents.extractor import QUOTE_MAX_CHARS, check_findings, extract_batch
from biolitsynth.llm import CallBudget, StageLLM
from biolitsynth.pubmed import TokenBucket
from biolitsynth.schemas import Direction, EvidenceDraft, Facet, Organism, StudyDesign, SubQuestion
from biolitsynth.store import Database
from biolitsynth.trace import Tracer

from ..fixture_papers import CASE_CONTROL, COHORT, META, PAPERS, RCT, VETERANS, cut
from ..llm_mock import ScriptedClient

SQS = ["sq1", "sq2"]
INVENTED = "Metformin eliminated all colorectal tumours within one week of dosing."   # in no abstract
INVENTED_2 = "Every participant on metformin was cured of colorectal cancer by the end."

CC_QUOTE = cut(CASE_CONTROL, "No dose response", "was found.")
CC_RESULT = cut(CASE_CONTROL, "Metformin use prior", "0.004).")
META_QUOTE = cut(META, "Overall survival (HR", "non-metformin users.")
COHORT_QUOTE = cut(COHORT, "Metformin users with diabetes appear", "non-users.")
VET_QUOTE = cut(VETERANS, "Among colorectal cancer patients with diabetes, metformin", "P = 0.003)")


def draft(pmid=CASE_CONTROL, quote=CC_QUOTE, **kw) -> EvidenceDraft:
    base = dict(pmid=pmid, statement="Metformin users had a lower risk.", quote=quote, subject="metformin",
                outcome="crc risk", direction=Direction.DECREASES, organism=Organism.HUMAN,
                study_design=StudyDesign.CASE_CONTROL)
    return EvidenceDraft(**{**base, **kw})


def check(drafts, pmid=CASE_CONTROL, *, paper=None, attempt=1, first_n=1):
    return check_findings(paper or PAPERS[pmid], drafts, attempt=attempt, first_n=first_n, sub_question_ids=SQS,
                          model="m-1", prompt_version="extractor.v1")


# --- quotes ----------------------------------------------------------------------------------------
def test_exact_quote_is_verified_with_offsets_and_code_fields():
    items, failed, outcomes, warnings = check([draft()])
    [it] = items
    a = PAPERS[CASE_CONTROL].abstract
    assert failed == [] and warnings == []
    assert it.id == f"ev_{CASE_CONTROL}_1" and it.pmid == CASE_CONTROL and it.sub_question_ids == SQS
    assert it.quote == CC_QUOTE == a[it.quote_start:it.quote_end]
    assert (it.extractor_model, it.prompt_version) == ("m-1", "extractor.v1")
    assert (it.canonical_subject, it.canonical_outcome, it.study_id) == (None, None, None)
    [o] = outcomes
    assert (o.pmid, o.attempt, o.seq, o.status, o.score, o.evidence_id) == (CASE_CONTROL, 1, 1, "exact", None, it.id)


def test_normalized_quote_is_stored_as_the_abstract_text():
    typed = CC_RESULT.replace("\xa0", " ")           # the abstract has non-breaking spaces around "="
    assert typed != CC_RESULT and typed not in PAPERS[CASE_CONTROL].abstract
    [it], failed, [o], _ = check([draft(quote=typed)])
    assert o.status == "normalized" and failed == []
    assert it.quote == CC_RESULT and PAPERS[CASE_CONTROL].abstract[it.quote_start:it.quote_end] == CC_RESULT


def test_fuzzy_quote_snaps_to_the_abstract_and_records_the_score():
    typed = COHORT_QUOTE.replace("significantly", "significanty")
    assert typed not in PAPERS[COHORT].abstract
    [it], failed, [o], _ = check([draft(COHORT, typed)], COHORT)
    assert o.status == "fuzzy" and o.score is not None and o.score >= 95 and o.evidence_id == it.id
    a = PAPERS[COHORT].abstract
    assert it.quote == a[it.quote_start:it.quote_end] and it.quote.startswith("Metformin users with diabetes appear")
    assert failed == []


def test_quote_not_in_the_abstract_fails():
    d = draft(quote=INVENTED)
    items, failed, [o], _ = check([d])
    assert items == [] and failed == [d]
    assert (o.status, o.evidence_id) == ("not_found", None)


def test_quote_shorter_than_20_characters_fails_without_looking_in_the_abstract():
    short = PAPERS[CASE_CONTROL].abstract[:19]
    d = draft(quote=short)
    items, failed, [o], _ = check([d])
    assert items == [] and failed == [d] and o.status == "too_short"


def test_a_quote_of_exactly_20_characters_is_long_enough():
    q = PAPERS[CASE_CONTROL].abstract[:20]
    items, failed, [o], _ = check([draft(quote=q)])
    assert len(items) == 1 and o.status == "exact"


def test_quote_longer_than_600_characters_fails():
    long_ = PAPERS[CASE_CONTROL].abstract[:QUOTE_MAX_CHARS + 1]
    d = draft(quote=long_)
    items, failed, [o], _ = check([d])
    assert items == [] and failed == [d] and o.status == "too_long"
    assert check([draft(quote=PAPERS[CASE_CONTROL].abstract[:QUOTE_MAX_CHARS])])[0] != []


def test_the_quote_is_stripped_before_the_length_and_span_checks():
    [it], _, [o], _ = check([draft(quote=f"  {CC_QUOTE}\n")])
    assert o.status == "exact" and it.quote == CC_QUOTE


def test_empty_statement_is_discarded_but_is_not_retried_or_counted_as_failed():
    items, failed, [o], warnings = check([draft(statement="   ")])
    assert items == [] and failed == []
    assert (o.status, o.evidence_id) == ("exact", None)
    assert warnings == [f"extract.empty_statement pmid:{CASE_CONTROL}"]


def test_outcomes_are_one_per_draft_in_draft_order_with_one_based_seq():
    drafts = [draft(quote=INVENTED), draft(), draft(quote=PAPERS[CASE_CONTROL].abstract[:5])]
    items, failed, outcomes, _ = check(drafts)
    assert [(o.seq, o.status) for o in outcomes] == [(1, "not_found"), (2, "exact"), (3, "too_short")]
    assert [d.quote for d in failed] == [INVENTED, PAPERS[CASE_CONTROL].abstract[:5]]
    assert [i.id for i in items] == [f"ev_{CASE_CONTROL}_1"]       # numbering counts verified items only


def test_item_numbering_starts_at_first_n_and_counts_only_verified_items():
    drafts = [draft(statement="a"), draft(quote=INVENTED, statement="b"), draft(quote=CC_RESULT, statement="c")]
    items, _, _, _ = check(drafts, first_n=3)
    assert [i.id for i in items] == [f"ev_{CASE_CONTROL}_3", f"ev_{CASE_CONTROL}_4"]
    assert [i.statement for i in items] == ["a", "c"]


def test_attempt_number_is_copied_into_the_outcomes():
    _, _, [o], _ = check([draft()], attempt=2)
    assert o.attempt == 2


# --- fields set by code ------------------------------------------------------------------------------
def test_text_fields_are_normalised():
    [it], _, _, _ = check([draft(statement="  Lower risk.  ", subject=" Metformin ", outcome="CRC Risk",
                                 population="  adults  ", limitations=["  small n ", "", "  "])])
    assert (it.statement, it.subject, it.outcome, it.population) == ("Lower risk.", "metformin", "crc risk", "adults")
    assert it.limitations == ["small n"]


def test_an_empty_population_becomes_none():
    [it], _, _, _ = check([draft(population="   ")])
    assert it.population is None


def test_publication_type_overrides_the_models_design_and_says_so():
    assert "Meta-Analysis" in PAPERS[META].publication_types
    [it], _, _, warnings = check([draft(META, META_QUOTE, study_design=StudyDesign.COHORT)], META)
    assert it.study_design == StudyDesign.META_ANALYSIS
    assert warnings == [f"extract.design_from_pubtype pmid:{META} llm=cohort pubmed=meta_analysis"]


def test_no_warning_when_the_model_agrees_with_the_publication_type():
    [it], _, _, warnings = check([draft(META, META_QUOTE, study_design=StudyDesign.META_ANALYSIS)], META)
    assert it.study_design == StudyDesign.META_ANALYSIS and warnings == []


def test_the_models_design_is_used_when_the_publication_types_settle_nothing():
    assert seed_none(CASE_CONTROL)
    [it], _, _, warnings = check([draft(study_design=StudyDesign.CROSS_SECTIONAL)])
    assert it.study_design == StudyDesign.CROSS_SECTIONAL and warnings == []


def seed_none(pmid) -> bool:
    return not set(PAPERS[pmid].publication_types) & {"Meta-Analysis", "Systematic Review",
                                                      "Randomized Controlled Trial", "Case Reports"}


def test_strength_uses_the_design_after_the_override():
    # the model says cohort (human, n unknown -> low) but PubMed says meta-analysis -> high
    [it], _, _, _ = check([draft(META, META_QUOTE, study_design=StudyDesign.COHORT)], META)
    assert it.strength == "high"


def test_strength_is_low_for_non_human_even_for_a_meta_analysis():
    [it], _, _, _ = check([draft(META, META_QUOTE, organism=Organism.ANIMAL)], META)
    assert it.strength == "low"


@pytest.mark.parametrize("spelling", ["323430", "323,430", "323 430"])
def test_sample_size_is_kept_when_the_number_is_in_the_abstract_in_any_plain_spelling(spelling):
    paper = PAPERS[COHORT].model_copy(update={"abstract": PAPERS[COHORT].abstract.replace("323,430", spelling)})
    [it], _, _, warnings = check([draft(COHORT, COHORT_QUOTE, study_design=StudyDesign.COHORT, sample_size=323430)],
                                 paper=paper)
    assert it.sample_size == 323430 and warnings == []
    assert it.strength == "moderate"                 # human cohort with n >= 1000


def test_sample_size_not_in_the_abstract_is_dropped_and_strength_follows():
    d = draft(COHORT, COHORT_QUOTE, study_design=StudyDesign.COHORT, sample_size=500000)
    [it], _, _, warnings = check([d], COHORT)
    assert it.sample_size is None and it.strength == "low"        # cohort, unknown n
    assert warnings == [f"extract.sample_size_unverified pmid:{COHORT} n=500000"]


def test_sample_size_must_be_a_whole_number_in_the_abstract_not_a_fragment_of_one():
    # "2341 (1.33%)" is in the abstract; 234 is only a prefix of it
    assert "2341" in PAPERS[COHORT].abstract
    [kept], _, _, w1 = check([draft(COHORT, COHORT_QUOTE, sample_size=2341)], COHORT)
    [cut_], _, _, w2 = check([draft(COHORT, COHORT_QUOTE, sample_size=234)], COHORT)
    assert kept.sample_size == 2341 and w1 == []
    assert cut_.sample_size is None and w2 == [f"extract.sample_size_unverified pmid:{COHORT} n=234"]


def test_an_unverified_sample_size_cannot_demote_an_rct_below_its_real_strength():
    # RCT with n unknown is high; a made-up n=40 would have made it moderate
    assert "Randomized Controlled Trial" in PAPERS[RCT].publication_types
    q = cut(RCT, "All associations were independent", "placebo.")
    [it], _, _, warnings = check([draft(RCT, q, study_design=StudyDesign.RCT, sample_size=40)], RCT)
    assert it.sample_size is None and it.strength == "high"
    assert f"extract.sample_size_unverified pmid:{RCT} n=40" in warnings


def test_a_verified_rct_sample_size_is_kept():
    q = cut(RCT, "All associations were independent", "placebo.")
    [it], _, _, warnings = check([draft(RCT, q, study_design=StudyDesign.RCT, sample_size=284)], RCT)
    assert it.sample_size == 284 and it.strength == "high" and warnings == []


def test_missing_sample_size_stays_none_without_a_warning():
    [it], _, _, warnings = check([draft()])
    assert it.sample_size is None and warnings == []


def test_effect_size_is_kept_stripped_when_it_is_in_the_abstract():
    es = cut(META, "HR 0.6, CI", "0.53-0.67")
    [it], _, _, warnings = check([draft(META, META_QUOTE, study_design=StudyDesign.META_ANALYSIS, effect_size=f"  {es} ")], META)
    assert it.effect_size == es and warnings == []


def test_effect_size_is_compared_after_normalisation():
    # the abstract writes "OR = 0.754" with non-breaking spaces; the model typed plain spaces
    assert "OR = 0.754" not in PAPERS[CASE_CONTROL].abstract
    [it], _, _, warnings = check([draft(effect_size="OR = 0.754")])
    assert it.effect_size == "OR = 0.754" and warnings == []


def test_effect_size_not_in_the_abstract_is_dropped_with_a_warning():
    [it], _, _, warnings = check([draft(META, META_QUOTE, study_design=StudyDesign.META_ANALYSIS, effect_size="HR 0.99")], META)
    assert it.effect_size is None
    assert warnings == [f"extract.effect_size_unverified pmid:{META}"]


def test_cohort_name_is_kept_when_in_the_abstract_ignoring_case():
    [a], _, _, w1 = check([draft(COHORT, COHORT_QUOTE, cohort_or_trial_name="NHIS-HEALS")], COHORT)
    [b], _, _, w2 = check([draft(COHORT, COHORT_QUOTE, cohort_or_trial_name=" nhis-heals ")], COHORT)
    assert a.cohort_or_trial_name == "NHIS-HEALS" and b.cohort_or_trial_name == "nhis-heals" and w1 == w2 == []


def test_cohort_name_may_come_from_the_title():
    paper = PAPERS[COHORT].model_copy(update={"title": "The UKPDS follow-up. " + PAPERS[COHORT].title})
    assert "UKPDS" not in paper.abstract
    [it], _, _, warnings = check([draft(COHORT, COHORT_QUOTE, cohort_or_trial_name="UKPDS")], paper=paper)
    assert it.cohort_or_trial_name == "UKPDS" and warnings == []


def test_cohort_name_not_in_title_or_abstract_is_dropped_with_a_warning():
    [it], _, _, warnings = check([draft(COHORT, COHORT_QUOTE, cohort_or_trial_name="UKPDS")], COHORT)
    assert it.cohort_or_trial_name is None
    assert warnings == [f"extract.cohort_name_unverified pmid:{COHORT}"]


# --- extract_batch --------------------------------------------------------------------------------
def sq(i: int) -> SubQuestion:
    return SubQuestion(id=f"sq{i}", text=f"sub-question number {i}", facet=Facet.OTHER, rationale="r", key_concepts=[])


def finding(pmid, quote, statement="a finding", **kw) -> dict:
    return {"pmid": pmid, "statement": statement, "quote": quote, "subject": "metformin", "outcome": "crc risk",
            "direction": "decreases", "organism": "human", "study_design": "case_control", **kw}


@pytest.fixture
def make_llm(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_run("r1", "q", {})

    def make(client):
        return StageLLM(client, model="m-1", stage="extract", agent="extractor", db=db, tracer=Tracer(db, "r1"),
                        limiter=TokenBucket(10_000), semaphore=asyncio.Semaphore(4), budget=CallBudget(None))
    make.db = db
    yield make
    db.close()


def batch_papers(*pmids):
    return [PAPERS[p] for p in pmids], {p: [sq(1)] if p != META else [sq(1), sq(2)] for p in pmids}


def script(first_pmid, first_reply, retries=()):
    """Retry replies first: a retry prompt also contains the paper's tag, but only it contains the rejected quote."""
    return ScriptedClient([*((rejected, {"findings": reply}) for rejected, reply in retries),
                           (f'pmid="{first_pmid}"', {"findings": first_reply})])


async def test_batch_prompt_has_every_abstract_and_all_kept_for_sub_questions(make_llm):
    papers, by = batch_papers(CASE_CONTROL, META)
    client = script(CASE_CONTROL, [finding(CASE_CONTROL, CC_QUOTE)])
    out = await extract_batch(papers, by, make_llm(client), parent=None)
    prompt = client.calls[0]["prompt"]
    assert f'<abstract pmid="{CASE_CONTROL}" sub_questions="sq1">' in prompt
    assert f'<abstract pmid="{META}" sub_questions="sq1,sq2">' in prompt
    assert PAPERS[META].abstract in prompt and "sub-question number 2" in prompt
    assert [r.pmid for r in out] == [CASE_CONTROL, META]
    events = [dict(e) for e in make_llm.db.trace_events("r1") if e["event_type"] == "llm_call"]
    assert len(events) == 1 and f"pmid:{META}" in json.loads(events[0]["input_refs_json"])


async def test_findings_are_grouped_by_paper_and_numbered_per_paper(make_llm):
    papers, by = batch_papers(CASE_CONTROL, META)
    client = script(CASE_CONTROL, [finding(META, META_QUOTE), finding(CASE_CONTROL, CC_QUOTE),
                                   finding(CASE_CONTROL, CC_RESULT)])
    a, b = await extract_batch(papers, by, make_llm(client))
    assert [i.id for i in a.items] == [f"ev_{CASE_CONTROL}_1", f"ev_{CASE_CONTROL}_2"]
    assert [i.id for i in b.items] == [f"ev_{META}_1"] and b.items[0].sub_question_ids == ["sq1", "sq2"]
    assert a.items[0].sub_question_ids == ["sq1"]
    assert (a.retried, a.dropped, b.retried, b.dropped) == (False, 0, False, 0)
    assert len(client.calls) == 1                                    # everything verified: no retry call


async def test_a_finding_for_a_pmid_outside_the_batch_is_discarded_and_noted_on_the_first_paper(make_llm):
    papers, by = batch_papers(CASE_CONTROL, META)
    client = script(CASE_CONTROL, [finding("99999999", CC_QUOTE), finding(META, META_QUOTE)])
    a, b = await extract_batch(papers, by, make_llm(client))
    assert "extract.unknown_pmid 99999999" in a.warnings and "extract.unknown_pmid 99999999" not in b.warnings
    assert a.items == [] and len(b.items) == 1
    assert len(client.calls) == 1 and a.outcomes == []               # not retried, not logged


async def test_more_than_five_findings_for_one_paper_keeps_the_first_five(make_llm):
    papers, by = batch_papers(CASE_CONTROL)
    many = [finding(CASE_CONTROL, CC_QUOTE, statement=f"finding {i}") for i in range(1, 8)]
    [r] = await extract_batch(papers, by, make_llm(script(CASE_CONTROL, many)))
    assert [i.statement for i in r.items] == [f"finding {i}" for i in range(1, 6)]
    assert f"extract.too_many_findings pmid:{CASE_CONTROL} got=7" in r.warnings
    assert len(r.outcomes) == 5


async def test_retry_asks_only_for_the_failed_finding_and_keeps_the_first_attempt_item(make_llm):
    papers, by = batch_papers(CASE_CONTROL)
    first = [finding(CASE_CONTROL, CC_QUOTE, statement="GOOD-STATEMENT-ALPHA"),
             finding(CASE_CONTROL, INVENTED, statement="BAD-STATEMENT-BETA")]
    retry = [finding(CASE_CONTROL, CC_RESULT, statement="RETRIED-STATEMENT-GAMMA")]
    client = script(CASE_CONTROL, first, retries=[(INVENTED, retry)])
    [r] = await extract_batch(papers, by, make_llm(client))
    assert len(client.calls) == 2
    retry_prompt = client.calls[1]["prompt"]
    assert INVENTED in retry_prompt and "BAD-STATEMENT-BETA" in retry_prompt
    assert "GOOD-STATEMENT-ALPHA" not in retry_prompt                # the verified finding is not re-asked
    assert [(i.id, i.statement) for i in r.items] == [(f"ev_{CASE_CONTROL}_1", "GOOD-STATEMENT-ALPHA"),
                                                      (f"ev_{CASE_CONTROL}_2", "RETRIED-STATEMENT-GAMMA")]
    assert (r.retried, r.dropped) == (True, 0)
    assert [(o.attempt, o.seq, o.status) for o in r.outcomes] == [(1, 1, "exact"), (1, 2, "not_found"),
                                                                  (2, 1, "exact")]
    assert [o.evidence_id for o in r.outcomes] == [f"ev_{CASE_CONTROL}_1", None, f"ev_{CASE_CONTROL}_2"]


async def test_a_finding_that_fails_again_after_the_retry_is_dropped_and_there_is_no_second_retry(make_llm):
    papers, by = batch_papers(CASE_CONTROL)
    client = script(CASE_CONTROL, [finding(CASE_CONTROL, INVENTED)],
                    retries=[(INVENTED, [finding(CASE_CONTROL, INVENTED_2)])])
    [r] = await extract_batch(papers, by, make_llm(client))
    assert len(client.calls) == 2                                    # a third call would raise (no reply left)
    assert r.items == [] and (r.retried, r.dropped) == (True, 1)
    assert [(o.attempt, o.status) for o in r.outcomes] == [(1, "not_found"), (2, "not_found")]


async def test_a_finding_the_model_omits_on_retry_counts_as_dropped(make_llm):
    papers, by = batch_papers(CASE_CONTROL)
    client = script(CASE_CONTROL, [finding(CASE_CONTROL, INVENTED)], retries=[(INVENTED, [])])
    [r] = await extract_batch(papers, by, make_llm(client))
    assert r.items == [] and (r.retried, r.dropped) == (True, 1)
    assert [(o.attempt, o.status) for o in r.outcomes] == [(1, "not_found")]


async def test_each_paper_with_a_failure_is_retried_once_and_papers_without_are_not(make_llm):
    papers, by = batch_papers(CASE_CONTROL, META, COHORT)
    first = [finding(CASE_CONTROL, INVENTED, statement="cc"), finding(META, INVENTED_2, statement="meta"),
             finding(COHORT, COHORT_QUOTE, statement="cohort ok")]
    client = script(CASE_CONTROL, first, retries=[
        (INVENTED, [finding(CASE_CONTROL, CC_QUOTE)]), (INVENTED_2, [finding(META, META_QUOTE)])])
    cc, meta, cohort = await extract_batch(papers, by, make_llm(client))
    assert len(client.calls) == 3
    retry_prompts = [c["prompt"] for c in client.calls[1:]]
    assert sorted(INVENTED in p for p in retry_prompts) == [False, True]
    assert (cc.retried, meta.retried, cohort.retried) == (True, True, False)
    assert [len(r.items) for r in (cc, meta, cohort)] == [1, 1, 1]
    assert cc.items[0].id == f"ev_{CASE_CONTROL}_1" and meta.items[0].id == f"ev_{META}_1"


async def test_retry_reply_keeps_only_this_papers_findings_and_at_most_as_many_as_failed(make_llm):
    papers, by = batch_papers(CASE_CONTROL, META)
    retry = [finding(META, META_QUOTE), finding(CASE_CONTROL, CC_QUOTE, statement="one"),
             finding(CASE_CONTROL, CC_RESULT, statement="two")]
    client = script(CASE_CONTROL, [finding(CASE_CONTROL, INVENTED)], retries=[(INVENTED, retry)])
    cc, meta = await extract_batch(papers, by, make_llm(client))
    assert [i.statement for i in cc.items] == ["one"] and meta.items == []
    assert cc.dropped == 0


async def test_a_paper_with_no_findings_is_normal(make_llm):
    papers, by = batch_papers(CASE_CONTROL, META)
    client = script(CASE_CONTROL, [finding(META, META_QUOTE)])
    cc, meta = await extract_batch(papers, by, make_llm(client))
    assert (cc.pmid, cc.items, cc.outcomes, cc.dropped, cc.retried) == (CASE_CONTROL, [], [], 0, False)
    assert len(meta.items) == 1


async def test_an_llm_error_is_not_caught(make_llm):
    papers, by = batch_papers(CASE_CONTROL)
    with pytest.raises(AssertionError):                    # ScriptedClient with no matching reply
        await extract_batch(papers, by, make_llm(ScriptedClient([])))
