"""PubMed efetch XML -> Paper (plan §5.3, §5.4).

Hazards this file exists to handle, each covered by a recorded fixture:
  * Inline markup (<i>, <sup>, <sub>, <b>) inside <AbstractText>: `.text` would drop everything after the first
    tag, so text is collected with itertext().
  * The article's own DOI is in PubmedData/ArticleIdList; every cited reference has its own ArticleIdList too.
    All lookups are scoped to the article, never `.//`.
  * PubDate may be <MedlineDate> ("1994 Dec 22-29") instead of <Year>.
  * Articles without an abstract (letters, errata) are skipped and reported, not stored.
NCBI sends non-ASCII as numeric references (&#x2013;); the parsed text therefore contains real en dashes,
non-breaking hyphens and thin spaces. The abstract is stored exactly as parsed. Normalisation is the quote
validator's job.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

from lxml import etree

from ..schemas import Paper

RETRACTED_TYPE = "Retracted Publication"
# Only trial registries identify a study. Other DataBank entries (GenBank, PDB, ...) are not study ids.
REGISTRY_BANKS = frozenset({"ClinicalTrials.gov", "ISRCTN"})
_REGISTRY_RE = re.compile(r"NCT\d{8}|ISRCTN\d{8}")
_YEAR_RE = re.compile(r"\b(\d{4})\b")


@dataclass(frozen=True)
class SkippedArticle:
    pmid: str
    reason: Literal["no_abstract", "not_a_journal_article"]


@dataclass
class ParseResult:
    papers: list[Paper] = field(default_factory=list)
    skipped: list[SkippedArticle] = field(default_factory=list)


def _xml_parser() -> etree.XMLParser:
    # The input is downloaded text: never fetch the DTD it points at, never expand entities.
    return etree.XMLParser(load_dtd=False, no_network=True, resolve_entities=False,
                           remove_comments=True, remove_pis=True)


def _root(xml: bytes | str) -> etree._Element:
    return etree.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml, _xml_parser())


def _text(el: etree._Element | None) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _year(article: etree._Element) -> int | None:
    pub = article.find("Journal/JournalIssue/PubDate")
    if pub is not None:
        if (y := pub.findtext("Year")) and y.strip().isdigit():
            return int(y)
        if m := _YEAR_RE.search(pub.findtext("MedlineDate") or ""):
            return int(m.group(1))
    y = article.findtext("ArticleDate/Year")
    return int(y) if y and y.strip().isdigit() else None


def _doi(pubmed_article: etree._Element, article: etree._Element) -> str | None:
    own = pubmed_article.find("PubmedData/ArticleIdList/ArticleId[@IdType='doi']")
    if own is not None and own.text:
        return own.text.strip()
    loc = article.find("ELocationID[@EIdType='doi']")
    return loc.text.strip() if loc is not None and loc.text else None


def _abstract(article: etree._Element) -> str:
    """Structured abstracts become 'LABEL: text' lines; plain abstracts stay as written."""
    parts = []
    for node in article.findall("Abstract/AbstractText"):
        text = _text(node)
        if text:
            label = node.get("Label")
            parts.append(f"{label}: {text}" if label else text)
    return "\n".join(parts)


def _authors(article: etree._Element) -> list[str]:
    out = []
    for a in article.findall("AuthorList/Author"):
        last, initials = a.findtext("LastName"), a.findtext("Initials")
        if last:
            out.append(f"{last} {initials}".strip())
        elif collective := a.findtext("CollectiveName"):
            out.append(collective.strip())
    return out


def _registry_ids(article: etree._Element, abstract: str) -> list[str]:
    ids: list[str] = []
    for bank in article.findall("DataBankList/DataBank"):
        if bank.findtext("DataBankName") in REGISTRY_BANKS:
            ids += [n.text.strip() for n in bank.findall("AccessionNumberList/AccessionNumber") if n.text]
    ids += _REGISTRY_RE.findall(abstract)
    return list(dict.fromkeys(ids))


def parse_pubmed_xml(xml: bytes | str) -> ParseResult:
    result = ParseResult()
    for pa in _root(xml):
        if pa.tag != "PubmedArticle":
            if (pmid := pa.findtext("BookDocument/PMID")):
                result.skipped.append(SkippedArticle(pmid.strip(), "not_a_journal_article"))
            continue
        citation = pa.find("MedlineCitation")
        article = citation.find("Article")
        pmid = citation.findtext("PMID").strip()
        abstract = _abstract(article)
        if not abstract:
            result.skipped.append(SkippedArticle(pmid, "no_abstract"))
            continue
        pub_types = [_text(t) for t in article.findall("PublicationTypeList/PublicationType")]
        result.papers.append(Paper(
            pmid=pmid,
            title=_text(article.find("ArticleTitle")),
            abstract=abstract,
            abstract_sha256=hashlib.sha256(abstract.encode("utf-8")).hexdigest(),
            authors=_authors(article),
            journal=article.findtext("Journal/Title"),
            year=_year(article),
            doi=_doi(pa, article),
            publication_types=pub_types,
            mesh_terms=[_text(d) for d in citation.findall("MeshHeadingList/MeshHeading/DescriptorName")],
            retrieved_by=[], rank_by_query={},
            registry_ids=_registry_ids(article, abstract),
            is_retracted=RETRACTED_TYPE in pub_types,
        ))
    return result


def split_articles(xml: bytes | str) -> dict[str, bytes]:
    """One standalone efetch document per PMID, for the per-PMID cache (plan §5.3)."""
    out: dict[str, bytes] = {}
    for pa in _root(xml):
        pmid = pa.findtext("MedlineCitation/PMID") or pa.findtext("BookDocument/PMID")
        if pmid:
            out[pmid.strip()] = b"<PubmedArticleSet>" + etree.tostring(pa, encoding="utf-8") + b"</PubmedArticleSet>"
    return out
