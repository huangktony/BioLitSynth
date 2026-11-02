import pytest

from biolitsynth.pubmed import dedup_papers, parse_pubmed_xml

from ..pubmed_mock import fixture


def papers(*names):
    out = {}
    for n in names:
        for p in parse_pubmed_xml(fixture(n)).papers:
            out[p.pmid] = p
    return out


def test_pmid_found_by_two_queries_becomes_one_paper_with_both_provenances():
    ps = papers("structured_rct_42897521")
    r = dedup_papers([("42897521", "q1", 4), ("42897521", "q3", 17)], ps)
    (p,) = r.papers.values()
    assert p.retrieved_by == ["q1", "q3"] and p.rank_by_query == {"q1": 4, "q3": 17}
    assert r.dropped == []


def test_retracted_paper_is_dropped_and_logged_with_the_plan_key():
    ps = papers("structured_rct_42897521", "retracted_rct_registry_41911553")
    r = dedup_papers([("42897521", "q1", 1), ("41911553", "q1", 2)], ps)
    assert list(r.papers) == ["42897521"]
    (d,) = r.dropped
    assert (d.pmid, d.reason, d.log_key) == ("41911553", "retracted", "dedup.retracted_dropped")


@pytest.mark.parametrize("name,pmid", [("retraction_notice_42964977", "42964977"), ("erratum_42930381", "42930381")])
def test_notices_and_errata_are_dropped(name, pmid):
    r = dedup_papers([(pmid, "q1", 1)], papers(name))
    assert r.papers == {} and [(d.pmid, d.reason) for d in r.dropped] == [(pmid, "notice")]


def test_old_plan_name_for_retraction_notices_is_also_caught():
    p = papers("structured_rct_42897521")["42897521"].model_copy(
        update={"publication_types": ["Journal Article", "Retraction of Publication"]})
    assert dedup_papers([("42897521", "q1", 1)], {"42897521": p}).dropped[0].reason == "notice"


def test_retraction_is_caught_by_pub_type_even_if_the_flag_is_unset():
    p = papers("retracted_rct_registry_41911553")["41911553"].model_copy(update={"is_retracted": False})
    assert dedup_papers([("41911553", "q1", 1)], {"41911553": p}).dropped[0].reason == "retracted"


def test_entries_without_a_stored_paper_are_ignored():
    # A paper with no abstract is never stored, so its run_papers row must not crash dedup.
    assert dedup_papers([("999", "q1", 1)], {}).papers == {}


def test_dedup_does_not_mutate_the_stored_paper():
    ps = papers("structured_rct_42897521")
    dedup_papers([("42897521", "q1", 1)], ps)
    assert ps["42897521"].retrieved_by == []
