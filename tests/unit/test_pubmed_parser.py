"""Expected values were read off the raw recorded XML by a person, not produced by the parser."""
import hashlib
import re

import pytest

from biolitsynth.pubmed import parse_pubmed_xml, split_articles

from ..pubmed_mock import CRC_IDS, fixture


TAG = re.compile(r"</?[A-Za-z]")          # a leftover <i> or </sup>. Plain "p < 0.001" is real abstract text and must stay.


def one(name):
    r = parse_pubmed_xml(fixture(name))
    assert len(r.papers) == 1 and not r.skipped, r
    return r.papers[0]


# --- structured abstract, DOI scoping, non-ASCII -----------------------------------------------
def test_structured_abstract_keeps_labels_one_section_per_line():
    p = one("structured_rct_42897521")
    lines = p.abstract.split("\n")
    assert [l.split(":")[0] for l in lines] == ["BACKGROUND", "METHODS", "RESULTS", "CONCLUSION", "CLINICAL TRIAL"]
    assert lines[0].startswith("BACKGROUND: While randomized clinical trials (RCTs) establish the effica")
    assert lines[-1] == "CLINICAL TRIAL: Not applicable."


def test_own_doi_is_not_a_reference_doi():
    # This record cites 40 other papers that each have a DOI. Its own is the one under PubmedData.
    assert one("structured_rct_42897521").doi == "10.1186/s12902-026-02423-7"


def test_non_ascii_survives_exactly_as_parsed():
    a = one("structured_rct_42897521").abstract
    assert "1\xa0M (27·9%)" in a                 # non-breaking space and middle dot, from &#xa0; / &#xb7;
    assert "&#" not in a


def test_authors_title_journal_year_and_pubtypes():
    p = one("structured_rct_42897521")
    assert p.authors[:3] == ["Ruan X", "Li R", "Wang L"]
    assert p.year == 2026 and p.publication_types == ["Journal Article"]
    assert p.title and p.journal


# --- inline markup -----------------------------------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("inline_italic_rct_42964126", "In this post-hoc analysis of two randomized controlled trials"),
    ("inline_italic_rct_42964126", "In this exploratory post-hoc analysis, a smaller maternal TSH increase"),
    ("inline_sup_unstructured_42848283", "resulted in increased intracellular Fe2+ levels, malondialdehyde"),
])
def test_text_after_an_inline_tag_is_kept(name, expected):
    # `.text` would stop at <i> or <sup>. The words on both sides of the tag must be present, in order.
    p = one(name)
    assert expected in p.abstract
    assert not TAG.search(p.abstract)


def test_unstructured_abstract_has_no_label_prefix():
    a = one("inline_sup_unstructured_42848283").abstract
    assert "\n" not in a and not a.split(" ")[0].endswith(":")


def test_retracted_paper_with_heavy_markup_parses():
    p = one("retracted_inline_39502250")
    assert p.is_retracted and "Retracted Publication" in p.publication_types
    assert not TAG.search(p.abstract)


# --- dates -------------------------------------------------------------------------------------
def test_medlinedate_gives_the_year():
    p = one("medlinedate_7997269")                # <MedlineDate>1994 Dec 22-29</MedlineDate>
    assert p.year == 1994 and p.doi == "10.1038/372794a0"


# --- registry ids and retraction flags ---------------------------------------------------------
def test_registry_id_from_databank_list():
    p = one("registry_protocol_42787272")
    assert p.registry_ids == ["NCT06049186"] and "Clinical Trial Protocol" in p.publication_types


def test_retracted_rct_flagged_and_keeps_its_registry_id():
    p = one("retracted_rct_registry_41911553")
    assert p.is_retracted and p.registry_ids == ["NCT06128967"]       # databank + abstract regex, de-duplicated


def test_no_registry_ids_when_there_are_none():
    assert one("structured_rct_42897521").registry_ids == []


def test_notice_and_erratum_types_are_visible_to_the_dedup_filter():
    assert "Retraction Notice" in one("retraction_notice_42964977").publication_types
    assert "Published Erratum" in one("erratum_42930381").publication_types


# --- skipped articles --------------------------------------------------------------------------
@pytest.mark.parametrize("name,pmid", [("letter_no_abstract_42722381", "42722381"),
                                       ("retracted_no_abstract_41911555", "41911555")])
def test_no_abstract_is_skipped_and_reported(name, pmid):
    r = parse_pubmed_xml(fixture(name))
    assert r.papers == [] and [(s.pmid, s.reason) for s in r.skipped] == [(pmid, "no_abstract")]


# --- a real 25-paper batch ---------------------------------------------------------------------
def test_batch_of_25_keeps_esearch_order_and_reports_the_five_without_abstracts():
    r = parse_pubmed_xml(fixture("efetch_metformin_crc_25"))
    no_abs = ["28506970", "30385499", "32384400", "28506966", "21949227"]    # read from the raw XML
    assert [s.pmid for s in r.skipped] == no_abs
    assert [p.pmid for p in r.papers] == [i for i in CRC_IDS if i not in no_abs]
    assert len(r.papers) == 20


def test_every_paper_has_a_clean_abstract_and_a_matching_hash():
    for p in parse_pubmed_xml(fixture("efetch_metformin_crc_25")).papers:
        assert p.abstract == p.abstract.strip() and p.abstract
        assert p.abstract_sha256 == hashlib.sha256(p.abstract.encode()).hexdigest()
        assert not TAG.search(p.abstract)


def test_comparison_signs_in_abstract_text_are_kept_not_mistaken_for_markup():
    p = next(p for p in parse_pubmed_xml(fixture("efetch_metformin_crc_25")).papers if p.pmid == "28533437")
    assert "(P < 0.05)" in p.abstract


def test_splitting_into_per_pmid_files_loses_nothing():
    whole = parse_pubmed_xml(fixture("efetch_metformin_crc_25"))
    parts = split_articles(fixture("efetch_metformin_crc_25"))
    assert list(parts) == CRC_IDS
    again = [p for pmid in CRC_IDS for p in parse_pubmed_xml(parts[pmid]).papers]
    assert [p.model_dump() for p in again] == [p.model_dump() for p in whole.papers]


# --- untrusted input ---------------------------------------------------------------------------
def test_external_entities_are_not_expanded():
    evil = b"""<?xml version="1.0"?>
<!DOCTYPE PubmedArticleSet [<!ENTITY x SYSTEM "file:///etc/hosts">]>
<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID><Article>
<ArticleTitle>t</ArticleTitle><Abstract><AbstractText>before &x; after</AbstractText></Abstract>
</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""
    p = parse_pubmed_xml(evil).papers[0]
    assert "localhost" not in p.abstract and "127.0.0.1" not in p.abstract


# --- HAND-BUILT XML ----------------------------------------------------------------------------
# No recorded record has a reference list but no DOI of its own (checked all fixtures and 240 older PMC
# articles), so this one edge is built by hand. The expected value, None, is unambiguous.
def _article(own_ids="", eloc="", refs=""):
    return f"""<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>5</PMID><Article>
<ArticleTitle>t</ArticleTitle>{eloc}<Abstract><AbstractText>a finding</AbstractText></Abstract></Article></MedlineCitation>
<PubmedData><ArticleIdList><ArticleId IdType="pubmed">5</ArticleId>{own_ids}</ArticleIdList>
<ReferenceList><Reference><Citation>x</Citation><ArticleIdList>{refs}</ArticleIdList></Reference></ReferenceList>
</PubmedData></PubmedArticle></PubmedArticleSet>"""


def test_article_with_no_doi_of_its_own_does_not_borrow_a_cited_papers_doi():
    xml = _article(refs='<ArticleId IdType="doi">10.9999/cited-paper</ArticleId>')
    assert parse_pubmed_xml(xml).papers[0].doi is None


def test_doi_falls_back_to_elocationid_when_the_id_list_has_none():
    xml = _article(eloc='<ELocationID EIdType="doi" ValidYN="Y">10.1234/from-eloc</ELocationID>',
                   refs='<ArticleId IdType="doi">10.9999/cited-paper</ArticleId>')
    assert parse_pubmed_xml(xml).papers[0].doi == "10.1234/from-eloc"
