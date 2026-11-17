"""Every extractor prompt file in prompts/ must load, render both templates, and keep the house rules."""
import re

import pytest

from biolitsynth.llm import load_prompt
from biolitsynth.llm.prompts import PROMPTS_DIR
from biolitsynth.schemas import Facet, SubQuestion

from ..fixture_papers import CASE_CONTROL, META, PAPERS

FILES = sorted(PROMPTS_DIR.glob("extractor.v*.md"), key=lambda p: int(re.search(r"\.v(\d+)\.md$", p.name)[1]))
VERSIONS = [int(re.search(r"\.v(\d+)\.md$", p.name)[1]) for p in FILES]


def sq(i):
    return SubQuestion(id=f"sq{i}", text=f"sub-question number {i}", facet=Facet.OTHER, rationale="r", key_concepts=[])


def entry(pmid, sq_ids):
    p = PAPERS[pmid]
    return {"pmid": p.pmid, "title": p.title, "abstract": p.abstract, "publication_types": p.publication_types,
            "sub_question_ids": sq_ids}


def test_extractor_versions_were_found():
    assert 1 in VERSIONS


@pytest.mark.parametrize("v", VERSIONS)
def test_extract_template_renders_every_paper_and_sub_question(v):
    r = load_prompt("extractor", version=v).render(
        "extract", papers=[entry(CASE_CONTROL, ["sq1"]), entry(META, ["sq1", "sq2"])],
        sub_questions=[sq(1), sq(2)])
    assert r.version == f"extractor.v{v}"
    for pmid in (CASE_CONTROL, META):
        assert pmid in r.user and PAPERS[pmid].abstract in r.user
    assert "sq1" in r.user and "sq2" in r.user and "sub-question number 2" in r.user


@pytest.mark.parametrize("v", VERSIONS)
def test_retry_template_renders_the_paper_and_the_rejected_quote(v):
    rejected = "Metformin cured every patient within a week."
    r = load_prompt("extractor", version=v).render(
        "retry", paper=entry(META, ["sq1", "sq2"]), sub_questions=[sq(1), sq(2)],
        failed=[{"statement": "Metformin cured everyone.", "quote": rejected}])
    assert META in r.user and PAPERS[META].abstract in r.user and rejected in r.user
    assert "sq1" in r.user and "sq2" in r.user


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_prompt_file_has_no_em_dash(path):
    assert "—" not in path.read_text()


def test_highest_version_is_the_live_one():
    assert load_prompt("extractor").version == f"extractor.v{max(VERSIONS)}"
