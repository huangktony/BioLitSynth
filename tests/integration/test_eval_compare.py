"""Retrieval comparison eval end to end: scripted Query Builder, mock NCBI, FakeReranker, a tiny faiss index.

Needs faiss (skipped without it). All papers are recorded fixtures (tests/pubmed_mock.py). Labels:
  A 36645225  B 35979933  C 32533543  D 37195082  E 35780231  F 32446797  G 32592092   (all have abstracts)
  N 28506970  (no abstract: NCBI returns it, the parser skips it, so it is never "fetched")
  X 39502250  (has an abstract, but neither search finds it and it is not in the dense index)
Q1 relevant = {B, E, F, N, X}  (n_relevant 5).

Boolean list (PubMed order)  [A, B, C, D, N, E]                      n_boolean 6
Dense index holds A-G.  Query vector [1,0,0,0]; inner products with the doc vectors below give the dense list
  F=3.0 > B=2.0 > G=1.0 > A=0.5 > C=0.25 > D=0.1 > E=-1.0          -> dense list [F, B, G, A, C, D, E]  n_dense 7
Union = boolean + (dense not in boolean) = [A, B, C, D, N, E, F, G]; N is not returned by efetch -> n_fetched 7.
Reranker scores: E=9 F=8 B=7 G=6 A=5 C=4 D=3 (N: default 0, never scored).
relevant_in_index = |{B, E, F}| = 3  (N and X are not in the index).
"""
import json

import numpy as np
import pytest

faiss = pytest.importorskip("faiss")

from biolitsynth.eval.bioasq import BioasqQuestion  # noqa: E402
from biolitsynth.eval.compare import OPTIONS, run_compare_eval  # noqa: E402
from biolitsynth.pubmed import TokenBucket  # noqa: E402
from biolitsynth.rerank import FakeReranker  # noqa: E402
from biolitsynth.retrieval.dense import DenseIndex  # noqa: E402

from ..llm_mock import ScriptedClient  # noqa: E402
from ..pubmed_mock import FakeNcbi  # noqa: E402

A, B, C, D, E, F, G = "36645225", "35979933", "32533543", "37195082", "35780231", "32446797", "32592092"
N, X = "28506970", "39502250"
FINAL, FINAL2 = "metformin AND colorectal", "statin AND liver"
Q1 = BioasqQuestion("bq1", "Does metformin lower colorectal cancer risk?", "yesno", (B, E, F, N, X))
Q2 = BioasqQuestion("bq2", "Do statins protect the liver?", "yesno", ())          # no relevant PMIDs

VECS = {A: [0.5, 0, 0, 0], B: [2, 0, 0, 0], C: [0.25, 0, 0, 0], D: [0.1, 0, 0, 0], E: [-1, 0, 0, 0],
        F: [3, 0, 0, 0], G: [1, 0, 0, 0]}
SCORES = {E: 9.0, F: 8.0, B: 7.0, G: 6.0, A: 5.0, C: 4.0, D: 3.0}


def make_dense():
    idx = faiss.IndexIDMap(faiss.IndexFlatIP(4))
    idx.add_with_ids(np.array(list(VECS.values()), dtype=np.float32), np.array([int(p) for p in VECS], dtype=np.int64))
    return DenseIndex(idx, lambda texts: np.tile(np.array([[1, 0, 0, 0]], dtype=np.float32), (len(texts), 1)))


def make_ncbi():
    return FakeNcbi(searches={FINAL: [A, B, C, D, N, E], FINAL2: [C, D]}, totals={FINAL: 150, FINAL2: 150})


def make_client():
    return ScriptedClient([("colorectal cancer risk?\nFacet", {"query_string": FINAL, "rationale": "r"}),
                           ("protect the liver?", {"query_string": FINAL2, "rationale": "r"})])


async def run(settings, tmp_path, split="dev", reranker=None):
    return await run_compare_eval([Q1, Q2], settings, split=split, client=make_client(),
                                  reranker=reranker or FakeReranker(SCORES), dense=make_dense(),
                                  transport=make_ncbi().transport, ncbi_env={}, llm_limiter=TokenBucket(10_000),
                                  ncbi_limiter=TokenBucket(10_000), out_dir=tmp_path / f"out_{split}")


async def test_every_field_of_question_one(settings, tmp_path):
    report, path = await run(settings, tmp_path)
    r = report.questions[0]
    assert (r.id, r.question, r.n_relevant) == ("bq1", Q1.body, 5)
    assert (r.final_query, r.final_hits) == (FINAL, 150)
    assert (r.n_boolean, r.n_dense, r.n_fetched) == (6, 7, 7)
    assert r.relevant_in_index == 3                    # B, E, F
    # hybrid pool = boolean[:500] + dense[:200] items not already in it = [A,B,C,D,N,E] + [F,G]
    assert r.hybrid_only_boolean == 1                  # relevant in boolean[:500] but not dense[:200]: N
    assert r.hybrid_only_dense == 1                    # relevant in dense[:200] but not boolean[:500]: F
    assert r.rerank_pairs == 7                         # 7 fetched docs, empty cache
    assert r.dense_s >= 0 and r.fetch_s >= 0 and r.rerank_s >= 0
    assert set(r.options) == set(OPTIONS)

    def check(name, size, pool_recall, recall, precision, top25=None):
        o = r.options[name]
        assert o.pool_size == size, name
        assert o.pool_recall == pytest.approx(pool_recall), name
        assert o.recall_at_25 == pytest.approx(recall), name
        assert o.precision_at_25 == pytest.approx(precision), name
        if top25 is not None:
            assert o.top25 == top25, name

    # boolean_order_25: raw pool [A,B,C,D,N,E], no rerank, no fetch filter. relevant in it: B, N, E = 3.
    #   recall 3/5 = 0.6, precision 3/25 = 0.12, pool_recall 3/5 = 0.6
    check("boolean_order_25", 6, 0.6, 0.6, 0.12, [A, B, C, D, N, E])
    # boolean_500 / boolean_1000: pool [A,B,C,D,N,E] (6), N is not fetched so it drops out of the top 25;
    #   sorted by score: E9 B7 A5 C4 D3 -> [E,B,A,C,D]. relevant in top25: E, B = 2 -> recall 0.4, precision 2/25 = 0.08
    #   pool_recall still counts N: 3/5 = 0.6
    for name in ("boolean_500", "boolean_1000"):
        check(name, 6, 0.6, 0.4, 0.08, [E, B, A, C, D])
    # dense_order_25: raw dense list [F,B,G,A,C,D,E]; relevant in it: F, B, E = 3 -> 0.6, 0.12, pool_recall 0.6
    check("dense_order_25", 7, 0.6, 0.6, 0.12, [F, B, G, A, C, D, E])
    # dense_200 / dense_1000: same 7 docs, reranked: E9 F8 B7 G6 A5 C4 D3. relevant F, B, E = 3 -> 0.6, 0.12
    for name in ("dense_200", "dense_1000"):
        check(name, 7, 0.6, 0.6, 0.12, [E, F, B, G, A, C, D])
    # hybrid: pool [A,B,C,D,N,E,F,G] (8). Reranked top25 (N dropped): E F B G A C D.
    #   relevant in top25: E, F, B = 3 -> recall 0.6, precision 0.12. pool_recall: B, E, F, N = 4/5 = 0.8
    check("hybrid", 8, 0.8, 0.6, 0.12, [E, F, B, G, A, C, D])


async def test_question_with_no_relevant_pmids_scores_zero(settings, tmp_path):
    report, _ = await run(settings, tmp_path)
    r = report.questions[1]
    assert (r.n_relevant, r.n_boolean, r.n_dense) == (0, 2, 7)
    # union = [C,D] + dense not in it [F,B,G,A,E] = 7 docs, all with abstracts; the query differs from Q1, so no cache
    assert (r.n_fetched, r.rerank_pairs, r.relevant_in_index) == (7, 7, 0)
    assert (r.hybrid_only_boolean, r.hybrid_only_dense) == (0, 0)
    for o in r.options.values():
        assert (o.pool_recall, o.recall_at_25, o.precision_at_25) == (0.0, 0.0, 0.0)
    assert r.options["boolean_order_25"].top25 == [C, D]
    # boolean_500 reranked: pool [C,D], scores C=4 > D=3
    assert r.options["boolean_500"].top25 == [C, D]
    # hybrid pool = [C,D] + dense[:200] minus those = [C,D,F,B,G,A,E]; reranked E9 F8 B7 G6 A5 C4 D3
    assert r.options["hybrid"].top25 == [E, F, B, G, A, C, D]
    assert r.options["hybrid"].pool_size == 7


async def test_rerank_cache_and_saved_json(settings, tmp_path):
    first, path = await run(settings, tmp_path, split="dev")
    assert sum(q.rerank_pairs for q in first.questions) == 14 > 0
    second, _ = await run(settings, tmp_path, split="dev2")      # new split -> new eval_id (ids carry only seconds)
    assert [q.rerank_pairs for q in second.questions] == [0, 0]  # every (model, query, pmid) is cached
    # and the cached scores give the same ranking
    assert second.questions[0].options["hybrid"].top25 == first.questions[0].options["hybrid"].top25

    saved = json.loads(path.read_text())
    assert path.name == "compare.json"
    assert set(OPTIONS) <= set(saved["summary"].get("options", saved["summary"]))
    assert saved["split"] == "dev" and len(saved["questions"]) == 2


async def test_summary_means_over_questions(settings, tmp_path):
    report, _ = await run(settings, tmp_path)
    opts = report.summary().get("options", report.summary())
    # boolean_order_25: recall (0.6 + 0) / 2 = 0.3; precision (0.12 + 0) / 2 = 0.06; pool_recall 0.3
    o = opts["boolean_order_25"]
    assert o["recall_at_25"] == pytest.approx(0.3)
    assert o["precision_at_25"] == pytest.approx(0.06)
    assert o["pool_recall"] == pytest.approx(0.3)
    # hybrid: recall (0.6 + 0) / 2 = 0.3; pool_recall (0.8 + 0) / 2 = 0.4
    assert opts["hybrid"]["recall_at_25"] == pytest.approx(0.3)
    assert opts["hybrid"]["pool_recall"] == pytest.approx(0.4)
