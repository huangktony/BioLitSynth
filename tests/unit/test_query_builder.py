"""Query Builder (plan §5.2): code-owned decisions, and the draft → count → refine loop against a mock NCBI.

Hit counts are hand-chosen per query string, so every expected outcome below follows from the band rules alone.
"""
import asyncio

import pytest

from biolitsynth.agents.query_builder import (InvalidDraft, build_query, check_draft, count_hits, in_band,
                                              pick_final, refine_direction, to_pubmed_query)
from biolitsynth.config import QueryBand
from biolitsynth.llm import CallBudget, StageLLM
from biolitsynth.pubmed import PubMedClient, TokenBucket
from biolitsynth.schemas import Facet, QueryDraft, SubQuestion
from biolitsynth.store import Database
from biolitsynth.trace import Tracer

from ..llm_mock import ScriptedClient
from ..pubmed_mock import FakeNcbi

BAND = QueryBand(min_hits=20, max_hits=2000)
SQ = SubQuestion(id="sq1", text="Does metformin lower colorectal cancer incidence?", facet=Facet.EPIDEMIOLOGY,
                 rationale="r", key_concepts=["metformin", "colorectal neoplasms"])


@pytest.mark.parametrize("hits, want", [(0, "broaden"), (19, "broaden"), (20, None), (2000, None), (2001, "narrow")])
def test_direction_and_band_edges(hits, want):
    assert refine_direction(hits, BAND) == want
    assert in_band(hits, BAND) == (want is None)


@pytest.mark.parametrize("history, want", [
    ([("a", 5), ("b", 150), ("c", 300)], ("b", 150)),          # first in band
    ([("a", 10), ("b", 4000)], ("a", 10)),                     # 20/10 = 2.0 vs 4000/2000 = 2.0: tie, earlier wins
    ([("a", 5), ("b", 3000)], ("b", 3000)),                    # 4.0 vs 1.5
    ([("a", 0), ("b", 9000), ("c", 0)], ("b", 9000)),          # zero-hit attempts are never chosen over a non-zero one
    ([("a", 0), ("b", 0)], ("b", 0)),                          # all empty: the last attempt
])
def test_pick_final(history, want):
    assert pick_final(history, BAND) == want


def test_pick_final_needs_an_attempt():
    with pytest.raises(ValueError):
        pick_final([], BAND)


def test_check_draft_normalizes_whitespace_and_refuses_broken_queries():
    assert check_draft(QueryDraft(query_string="  (a OR b)\n AND  c ", rationale="")) == "(a OR b) AND c"
    for bad in ("   ", "(a OR b AND c"):
        with pytest.raises(InvalidDraft):
            check_draft(QueryDraft(query_string=bad, rationale=""))


@pytest.fixture
def env(tmp_path, settings):
    db = Database(tmp_path / "t.db")
    db.create_run("r1", "q", {})
    tracer = Tracer(db, "r1")

    def make(script, ncbi):
        client = ScriptedClient(script)
        llm = StageLLM(client, model="m", stage="query", agent="query_builder", db=db, tracer=tracer,
                       limiter=TokenBucket(10_000), semaphore=asyncio.Semaphore(1), budget=CallBudget(None))
        pubmed = PubMedClient(settings.ncbi, db, tmp_path / "cache", tracer=tracer, transport=ncbi.transport,
                              env={}, limiter=TokenBucket(10_000))
        return client, llm, pubmed
    yield db, make
    db.close()


def reply(q):
    return {"query_string": q, "rationale": "scripted"}


async def test_too_few_then_too_many_then_in_band(env):
    db, make = env
    ncbi = FakeNcbi(totals={"A": 3, "B": 5000, "C": 150})
    client, llm, pubmed = make([("Write the first query", reply("A")), ("Broaden it", reply("B")),
                                ("Narrow it", reply("C"))], ncbi)
    built = await build_query(SQ, llm, pubmed, BAND, max_refinements=2)
    assert (built.query_string, built.hit_count, built.history) == ("C", 150, [("A", 3), ("B", 5000), ("C", 150)])
    # code tells the model the direction and shows it every earlier attempt with its count
    assert "1. 3 hits: A" in client.calls[1]["prompt"] and "refinement 1 of 2" in client.calls[1]["prompt"]
    assert "2. 5000 hits: B" in client.calls[2]["prompt"] and "above the band" in client.calls[2]["prompt"]
    assert ncbi.search_terms() == [("A", "0"), ("B", "0"), ("C", "0")]          # count-only probes
    q = to_pubmed_query("q1", "sq1", built, retmax=100, keep_top=25)
    assert q.refinement_history == [("A", 3), ("B", 5000), ("C", 150)] and q.hit_count == 150
    await pubmed.aclose()


async def test_in_band_draft_is_not_refined(env):
    db, make = env
    client, llm, pubmed = make([("Write the first query", reply("A"))], FakeNcbi(totals={"A": 500}))
    built = await build_query(SQ, llm, pubmed, BAND, max_refinements=2)
    assert built.history == [("A", 500)] and len(client.calls) == 1
    await pubmed.aclose()


async def test_refinements_stop_at_the_cap_and_the_closest_attempt_is_kept(env):
    db, make = env
    ncbi = FakeNcbi(totals={"A": 9000, "B": 3000, "C": 50000})
    client, llm, pubmed = make([("first", reply("A")), ("Narrow", reply("B")), ("Narrow", reply("C"))], ncbi)
    built = await build_query(SQ, llm, pubmed, BAND, max_refinements=2)
    assert len(built.history) == 3 and (built.query_string, built.hit_count) == ("B", 3000)
    await pubmed.aclose()


async def test_a_query_pubmed_rejects_counts_as_zero_and_is_broadened(env):
    db, make = env
    ncbi = FakeNcbi(totals={"B": 40}, rejected={"A[bad"})
    client, llm, pubmed = make([("first", reply("A[bad")), ("Broaden", reply("B"))], ncbi)
    assert await count_hits("A[bad", pubmed) == 0
    built = await build_query(SQ, llm, pubmed, BAND, max_refinements=2)
    assert built.history == [("A[bad", 0), ("B", 40)]
    await pubmed.aclose()


def test_and_groups_split_only_at_the_top_level():
    from biolitsynth.agents.query_builder import and_groups
    assert and_groups("(a AND b OR c) AND d[mh] AND (e OR (f AND g))") == ["(a AND b OR c)", "d[mh]", "(e OR (f AND g))"]
    assert and_groups("single") == ["single"]


def test_concept_warnings_flag_missing_concepts_and_framing_groups_but_not_filters():
    from biolitsynth.agents.query_builder import concept_warnings
    concepts = ["metformin", "colorectal neoplasms"]
    ok = "(metformin[mh] OR metformin[tiab]) AND (colorectal neoplasms[mh] OR colon cancer[tiab]) AND humans[mh]"
    assert concept_warnings(ok, concepts) == []
    bad = "(metformin[mh]) AND (function[tiab] OR role[tiab])"
    w = concept_warnings(bad, concepts)
    assert any("key_concept_missing 'colorectal neoplasms'" in x for x in w)
    assert any("group_not_a_key_concept" in x and "function" in x for x in w)
    assert concept_warnings(bad, []) == []                      # nothing to check against
