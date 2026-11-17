"""Real MedCPT models. Opt-in: `pytest -m slow`. Inputs are recorded fixtures; titles/abstracts are read with plain
lxml so the code under test is not involved.

FOR A PERSON TO APPROVE
  question:   "Does metformin use reduce the risk of colorectal cancer?"
  on-topic:   PMID 32968891  "Metformin usage and the risk of colorectal cancer: a national cohort study" (2021)
  unrelated:  PMID 7997269   "Role of SAPK/ERK kinase-1 in the stress-activated pathway regulating transcription factor
              c-Jun" (kinase signalling, no cancer-risk content)
  dense:      PMID 32968891 (< 38,000,000, published 2021) must be found by searching its exact title.
"""
from pathlib import Path

import pytest
from lxml import etree

from biolitsynth.config import load_settings
from biolitsynth.rerank import CrossEncoderReranker

from ..pubmed_mock import ARTICLES

REPO = Path(__file__).resolve().parents[2]
QUESTION = "Does metformin use reduce the risk of colorectal cancer?"
ON_TOPIC, UNRELATED = "32968891", "7997269"


def title_and_abstract(pmid):
    root = etree.fromstring(ARTICLES[pmid])
    title = "".join(root.find(".//ArticleTitle").itertext()).strip()
    abstract = " ".join("".join(a.itertext()).strip() for a in root.findall(".//AbstractText"))
    return title, abstract


@pytest.mark.slow
def test_cross_encoder_prefers_the_on_topic_abstract():
    cfg = load_settings(REPO / "config.yaml").rerank
    docs = []
    for pmid in (ON_TOPIC, UNRELATED):
        t, a = title_and_abstract(pmid)
        docs.append((pmid, f"{t} {a}".strip()))
    on, off = CrossEncoderReranker(cfg).score(QUESTION, docs)
    assert on > off


@pytest.mark.slow
def test_dense_index_finds_a_paper_by_its_exact_title():
    from biolitsynth.retrieval.dense import DenseIndex

    cfg = load_settings(REPO / "config.yaml").dense
    if not Path(cfg.index_path).exists():
        pytest.skip(f"no dense index at {cfg.index_path}")
    assert int(ON_TOPIC) < 38_000_000
    dense = DenseIndex.from_config(cfg)
    title, _ = title_and_abstract(ON_TOPIC)
    assert ON_TOPIC in [p for p, _ in dense.search(title, 10)]
    assert dense.contains({ON_TOPIC, "99999999"}) == {ON_TOPIC}
