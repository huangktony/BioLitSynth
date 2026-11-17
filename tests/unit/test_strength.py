"""strength_for and seed_design, pinned to the table in the P3 spec (section 4.1). Each row is checked by hand."""
import pytest

from biolitsynth.agents.extractor import seed_design, strength_for
from biolitsynth.schemas import Organism, StudyDesign as D

H = Organism.HUMAN


@pytest.mark.parametrize("design", [D.META_ANALYSIS, D.SYSTEMATIC_REVIEW])
@pytest.mark.parametrize("n", [None, 3, 100000])
def test_meta_analysis_and_systematic_review_are_high_whatever_the_sample_size(design, n):
    assert strength_for(design, H, n) == "high"


@pytest.mark.parametrize("n, want", [(None, "high"), (50, "high"), (49, "moderate"), (1, "moderate"), (5000, "high")])
def test_rct_cut_is_at_50_and_unknown_size_is_high(n, want):
    assert strength_for(D.RCT, H, n) == want


@pytest.mark.parametrize("design", [D.COHORT, D.CASE_CONTROL])
@pytest.mark.parametrize("n, want", [(None, "low"), (999, "low"), (1000, "moderate"), (323430, "moderate")])
def test_cohort_and_case_control_cut_is_at_1000_and_unknown_size_is_low(design, n, want):
    assert strength_for(design, H, n) == want


@pytest.mark.parametrize("design", [D.CROSS_SECTIONAL, D.CASE_REPORT, D.ANIMAL, D.IN_VITRO, D.COMPUTATIONAL,
                                    D.NARRATIVE_REVIEW, D.OTHER])
@pytest.mark.parametrize("n", [None, 10, 1000000])
def test_the_remaining_designs_are_always_low(design, n):
    assert strength_for(design, H, n) == "low"


@pytest.mark.parametrize("organism", [Organism.ANIMAL, Organism.CELL_LINE, Organism.NOT_APPLICABLE])
@pytest.mark.parametrize("design", list(D))
def test_non_human_is_always_low(organism, design):
    assert strength_for(design, organism, 100000) == "low"


def test_every_design_is_covered_by_the_tables_above():
    covered = {D.META_ANALYSIS, D.SYSTEMATIC_REVIEW, D.RCT, D.COHORT, D.CASE_CONTROL, D.CROSS_SECTIONAL,
               D.CASE_REPORT, D.ANIMAL, D.IN_VITRO, D.COMPUTATIONAL, D.NARRATIVE_REVIEW, D.OTHER}
    assert covered == set(D)


@pytest.mark.parametrize("types, want", [
    (["Meta-Analysis"], D.META_ANALYSIS),
    (["Systematic Review"], D.SYSTEMATIC_REVIEW),
    (["Randomized Controlled Trial"], D.RCT),
    (["Case Reports"], D.CASE_REPORT),
])
def test_seed_design_each_publication_type(types, want):
    assert seed_design(types) == want


def test_seed_design_first_match_in_the_spec_order_wins_not_the_input_order():
    assert seed_design(["Case Reports", "Randomized Controlled Trial", "Systematic Review", "Meta-Analysis"]) \
        == D.META_ANALYSIS
    assert seed_design(["Randomized Controlled Trial", "Systematic Review"]) == D.SYSTEMATIC_REVIEW
    assert seed_design(["Case Reports", "Randomized Controlled Trial"]) == D.RCT
    assert seed_design(["Journal Article", "Meta-Analysis", "Systematic Review"]) == D.META_ANALYSIS   # a real PMID 32592092


@pytest.mark.parametrize("types, want", [(["meta-analysis"], D.META_ANALYSIS),
                                         (["SYSTEMATIC REVIEW"], D.SYSTEMATIC_REVIEW),
                                         (["randomized controlled trial"], D.RCT),
                                         (["case reports"], D.CASE_REPORT)])
def test_seed_design_ignores_case(types, want):
    assert seed_design(types) == want


@pytest.mark.parametrize("types", [[], ["Review"], ["Journal Article"], ["Randomized Controlled Trial, Veterinary"],
                                   ["Clinical Trial"], ["Case Report"], ["Meta-Analysis as Topic"],
                                   ["Journal Article", "Research Support, Non-U.S. Gov't"]])
def test_seed_design_needs_a_whole_string_match(types):
    assert seed_design(types) is None
