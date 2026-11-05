"""Quote span validator (plan §5.5) against the recorded PubMed abstracts.

The quotes are cut from the recorded abstracts by the test itself (every sentence of every fixture abstract), then
perturbed the way models perturb quotes. The expected answer is the original slice of the fixture, so nothing here
was written by the code's author except the perturbations.
"""
import re
from pathlib import Path

import pytest

from biolitsynth.checks.quote_span import FUZZY_MIN_CHARS, apply_span, normalize, validate_quote

from biolitsynth.pubmed import parse_pubmed_xml
from biolitsynth.schemas import Direction, EvidenceItem, Organism, StudyDesign

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "pubmed"
PAPERS = [p for f in sorted(FIX.glob("*.xml")) for p in parse_pubmed_xml(f.read_bytes()).papers]
# (abstract, start, end) for every sentence of 40+ characters in the recorded abstracts
SPANS = [(p.abstract, m.start(), m.end()) for p in PAPERS
         for m in re.finditer(r"[^\s.][^.\n]{39,}?[.]", p.abstract)]


def test_the_fixture_set_is_big_enough_to_mean_something():
    assert len(PAPERS) >= 20 and len(SPANS) >= 100


def test_every_exact_sentence_is_found_at_its_offsets():
    for abstract, s, e in SPANS:
        m = validate_quote(abstract, abstract[s:e])
        assert (m.status, m.start, m.end) == ("exact", s, e)


@pytest.mark.parametrize("perturb", [
    lambda t: t.replace(" ", "  "),                       # doubled spaces
    lambda t: t.replace(" ", "\n", 3),                    # line breaks
    lambda t: t.replace("-", "–"),                   # hyphen typed as en dash
    lambda t: t.replace("–", "-").replace("—", "-"),   # en/em dash typed as hyphen
    lambda t: "  " + t + " ",                             # padding
])
def test_whitespace_and_dash_variants_snap_back_to_the_original_text(perturb):
    statuses = set()
    for abstract, s, e in SPANS:
        q = perturb(abstract[s:e])
        m = validate_quote(abstract, q)
        assert (m.start, m.end, m.quote) == (s, e, abstract[s:e]), q
        statuses.add(m.status)
    assert statuses <= {"exact", "normalized"}


def test_one_dropped_character_snaps_by_fuzzy_match_to_nearly_the_same_span():
    n = 0
    for abstract, s, e in SPANS:
        if e - s < 80:
            continue
        mid = (s + e) // 2
        q = abstract[s:mid] + abstract[mid + 1:e]
        m = validate_quote(abstract, q)
        assert m.status == "fuzzy" and m.score >= 95
        overlap = max(0, min(e, m.end) - max(s, m.start))
        assert overlap >= 0.9 * (e - s) and abstract[m.start:m.end] == m.quote
        n += 1
    assert n >= 30


def test_normalize_keeps_an_index_back_into_the_original():
    t = "a\u2013b  c\u00adx\u201cy\u201d\n"
    n, idx = normalize(t)
    assert n == 'a-b cx"y"' and [t[i] for i in idx] == ["a", "\u2013", "b", " ", "c", "x", "\u201c", "y", "\u201d"]


def test_text_not_in_the_abstract_is_rejected_and_short_quotes_never_fuzzy_match():
    abstract = PAPERS[0].abstract
    assert validate_quote(abstract, "Aspirin abolished all migraines in every participant studied.").status == "not_found"
    assert validate_quote(abstract, "   ").status == "not_found"
    short = abstract[:FUZZY_MIN_CHARS - 2]
    assert validate_quote(abstract, short[:-1] + "#").status == "not_found"


def test_apply_span_sets_offsets_and_snaps_the_stored_quote():
    abstract, s, e = SPANS[0]
    item = EvidenceItem(id="ev_1_1", pmid="1", sub_question_ids=["sq1"], statement="x",
                        quote=abstract[s:e].replace(" ", "  "), subject="a", outcome="b",
                        direction=Direction.DECREASES, organism=Organism.HUMAN, study_design=StudyDesign.RCT,
                        extractor_model="m", prompt_version="extractor.v1")
    fixed, m = apply_span(item, abstract)
    assert (fixed.quote_start, fixed.quote_end, fixed.quote) == (s, e, abstract[s:e]) and m.status == "normalized"
    gone, m = apply_span(item.model_copy(update={"quote": "nothing like this appears anywhere at all"}), abstract)
    assert gone is None and m.status == "not_found"
