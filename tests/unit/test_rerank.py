"""Reranker helpers, pinned from the spec. Every expected value is worked out by hand in the comments."""
import hashlib

import pytest

from biolitsynth.rerank import FakeReranker, article_text, query_text, score_with_cache, select_top
from biolitsynth.schemas import Facet, SubQuestion
from biolitsynth.schemas.paper import Paper
from biolitsynth.store import Database

# Scores: 2 -> 0.9; 1, 3, 5 -> 0.5 (a three-way tie); 4 -> 0.1.
# pubmed_rank: 3 -> 1, 4 -> 2, 1 -> 3, 5 -> 3, 2 -> 5.
# Sort = score desc, then pubmed_rank asc, then pmid asc:  2 (0.9), 3 (0.5, pr1), 1 (0.5, pr3, pmid "1"),
# 5 (0.5, pr3, pmid "5"), 4 (0.1).  Ranks 1..5.  (pmids are all one digit, so string and numeric order agree.)
SCORED = [("1", 0.5), ("2", 0.9), ("3", 0.5), ("4", 0.1), ("5", 0.5)]
PR = {"1": 3, "2": 5, "3": 1, "4": 2, "5": 3}


def sel(**kw):
    args = dict(sub_question_id="sq1", top_x=3, min_score=None, model="m", pubmed_rank=PR)
    return select_top(SCORED, **{**args, **kw})


def test_order_ranks_and_tiebreaks():
    out = sel()
    assert [r.pmid for r in out] == ["2", "3", "1", "5", "4"]
    assert [r.rank for r in out] == [1, 2, 3, 4, 5]
    assert [r.score for r in out] == [0.9, 0.5, 0.5, 0.5, 0.1]
    assert [r.pubmed_rank for r in out] == [5, 1, 3, 3, 2]


def test_top_x_cut_without_floor():
    out = sel()
    assert [r.kept for r in out] == [True, True, True, False, False]
    assert [r.drop_reason for r in out] == [None, None, None, "below_top_x", "below_top_x"]


def test_floor_equal_to_score_is_kept_and_floor_beats_top_x_as_reason():
    out = sel(min_score=0.5)
    # 0.5 >= 0.5 so ranks 2-3 stay kept; "5" (rank 4, 0.5) fails only the top-x cut;
    # "4" (rank 5, 0.1) is both below the floor and beyond top 3 -> reason is below_min_score.
    assert [r.kept for r in out] == [True, True, True, False, False]
    assert [r.drop_reason for r in out] == [None, None, None, "below_top_x", "below_min_score"]


def test_high_floor_drops_inside_top_x():
    out = sel(min_score=0.6)
    # only "2" (0.9) clears 0.6; "3" and "1" are inside the top 3 but below the floor.
    assert [r.kept for r in out] == [True, False, False, False, False]
    assert [r.drop_reason for r in out] == [None] + ["below_min_score"] * 4


def test_top_x_larger_than_input_keeps_all():
    assert all(r.kept for r in sel(top_x=10))


def test_copies_model_and_sub_question_id():
    out = sel(model="ncbi/x", sub_question_id="sq7")
    assert {r.model for r in out} == {"ncbi/x"} and {r.sub_question_id for r in out} == {"sq7"}


def test_missing_pubmed_rank_raises_keyerror():
    with pytest.raises(KeyError):
        sel(pubmed_rank={"1": 1, "2": 2})


def sq(**kw):
    return SubQuestion(id="sq1", text="Does X help Y?", facet=Facet.OTHER, rationale="r", key_concepts=[], **kw)


def test_query_text():
    assert query_text(sq()) == "Does X help Y?"
    s = SubQuestion(id="sq1", text="Does X help Y?", facet=Facet.OTHER, rationale="r", key_concepts=["X", "Y z"])
    assert query_text(s) == "Does X help Y? X; Y z"


def paper(title, abstract):
    return Paper(pmid="1", title=title, abstract=abstract, abstract_sha256="0", authors=[], publication_types=[],
                 mesh_terms=[], retrieved_by=[], rank_by_query={})


def test_article_text():
    assert article_text(paper("A title.", "Some abstract.")) == "A title. Some abstract."
    # empty abstract: "A title. " stripped -> "A title."
    assert article_text(paper("A title.", "")) == "A title."


# ---- FakeReranker and the cache ---------------------------------------------------------------------------
def test_fake_reranker_scores_and_counts_docs():
    r = FakeReranker({"1": 2.0, "2": -1.0}, default=0.25)
    assert r.model == "fake-reranker" and r.calls == 0
    assert r.score("q", [("1", "t"), ("2", "t"), ("9", "t")]) == [2.0, -1.0, 0.25]
    assert r.calls == 3
    r.score("q", [("1", "t")])
    assert r.calls == 4       # counts docs, not calls
    assert FakeReranker({}, model="other").model == "other"


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


DOCS = [("1", "a"), ("2", "b"), ("3", "c")]
SC = {"1": 1.0, "2": 2.0, "3": 3.0, "4": 4.0}


def test_cache_first_call_scores_everything_second_scores_nothing(db):
    r = FakeReranker(SC)
    assert score_with_cache(r, db, "q", DOCS) == {"1": 1.0, "2": 2.0, "3": 3.0}
    assert r.calls == 3
    assert score_with_cache(r, db, "q", DOCS) == {"1": 1.0, "2": 2.0, "3": 3.0}
    assert r.calls == 3       # unchanged: all cached


def test_cache_partial_overlap_scores_only_new_docs(db):
    r = FakeReranker(SC)
    score_with_cache(r, db, "q", DOCS)
    out = score_with_cache(r, db, "q", [("2", "b"), ("3", "c"), ("4", "d")])
    assert out == {"2": 2.0, "3": 3.0, "4": 4.0}
    assert r.calls == 4       # 3 + only pmid 4


def test_cache_returns_stored_value_not_a_fresh_score(db):
    score_with_cache(FakeReranker({"1": 1.0}), db, "q", [("1", "a")])
    again = FakeReranker({"1": 99.0})
    assert score_with_cache(again, db, "q", [("1", "a")]) == {"1": 1.0}
    assert again.calls == 0


def test_cache_key_includes_query_and_model(db):
    r = FakeReranker(SC)
    score_with_cache(r, db, "q1", DOCS)
    score_with_cache(r, db, "q2", DOCS)
    assert r.calls == 6       # new query -> miss
    other = FakeReranker(SC, model="other")
    score_with_cache(other, db, "q1", DOCS)
    assert other.calls == 3   # new model name -> miss, even though (fake-reranker, q1) is cached
    again = FakeReranker(SC, model="other")
    score_with_cache(again, db, "q1", DOCS)
    assert again.calls == 0   # (other, q1) is now cached -> hit
    assert score_with_cache(FakeReranker(SC, model="third"), db, "q1", DOCS) == {"1": 1.0, "2": 2.0, "3": 3.0}


def test_db_methods_roundtrip_with_sha256_of_query(db):
    r = FakeReranker(SC)
    score_with_cache(r, db, "my query", DOCS)
    sha = hashlib.sha256("my query".encode()).hexdigest()
    assert db.get_rerank_scores("fake-reranker", sha, ["1", "3", "zz"]) == {"1": 1.0, "3": 3.0}
    db.put_rerank_scores("fake-reranker", sha, [("zz", 7.5)])
    assert db.get_rerank_scores("fake-reranker", sha, ["zz"]) == {"zz": 7.5}
    assert db.get_rerank_scores("fake-reranker", "0" * 64, ["1"]) == {}
