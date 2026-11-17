"""DenseIndex on tiny hand-made faiss indexes. Inner product, not cosine: a longer vector in the same direction wins."""
import numpy as np
import pytest

faiss = pytest.importorskip("faiss")

from biolitsynth.retrieval.dense import DenseIndex  # noqa: E402

# d=4. Query [1,0,0,0]. Inner product of each doc with the query is its first component:
#   111 -> 3.0 (same direction, longest), 222 -> 2.0, 333 -> 0.5, 444 -> 0.0 (orthogonal), 555 -> -1.0
VECS = {111: [3, 0, 0, 0], 222: [2, 1, 0, 0], 333: [0.5, 0, 1, 0], 444: [0, 0, 0, 5], 555: [-1, 0, 0, 0]}
QUERY = np.array([[1, 0, 0, 0]], dtype=np.float32)


def encode(texts):
    return np.repeat(QUERY, len(texts), axis=0)


def flat_index():
    idx = faiss.IndexIDMap(faiss.IndexFlatIP(4))
    ids = np.array(list(VECS), dtype=np.int64)
    idx.add_with_ids(np.array([VECS[i] for i in VECS], dtype=np.float32), ids)
    return idx


def test_search_order_and_scores():
    hits = DenseIndex(flat_index(), encode).search("anything", 3)
    assert [p for p, _ in hits] == ["111", "222", "333"]
    assert [s for _, s in hits] == pytest.approx([3.0, 2.0, 0.5])
    assert all(isinstance(p, str) for p, _ in hits)


def test_k_larger_than_index_drops_minus_one_ids():
    hits = DenseIndex(flat_index(), encode).search("anything", 50)
    assert [p for p, _ in hits] == ["111", "222", "333", "444", "555"]
    assert hits[-1][1] == pytest.approx(-1.0)


def test_contains():
    d = DenseIndex(flat_index(), encode)
    assert d.contains(["111", "555", "999", "12"]) == {"111", "555"}
    assert d.contains([]) == set()
    assert d.contains(iter(["444"])) == {"444"}


_KEEP = []   # faiss objects don't take new attributes; hold quantizers here so they outlive the index


def ivf_index(nlist=3):
    rng = np.random.default_rng(0)
    train = rng.standard_normal((60, 4)).astype(np.float32)
    quant = faiss.IndexFlatIP(4)
    idx = faiss.IndexIVFFlat(quant, 4, nlist, faiss.METRIC_INNER_PRODUCT)
    idx.train(train)
    ids = np.array([1001, 1002, 1003, 1004, 1005, 1006], dtype=np.int64)
    idx.add_with_ids(train[:6], ids)
    _KEEP.append(quant)          # keep the quantizer alive
    return idx


def test_ivf_contains_walks_inverted_lists():
    d = DenseIndex(ivf_index(), encode)
    assert d.contains(["1001", "1006", "1007", "5"]) == {"1001", "1006"}


def test_nprobe_is_applied_to_ivf():
    idx = ivf_index(4)
    DenseIndex(idx, encode, nprobe=3)
    assert faiss.extract_index_ivf(idx).nprobe == 3


def test_ivf_search_with_full_probe_finds_exact_neighbour():
    idx = ivf_index(3)
    d = DenseIndex(idx, encode, nprobe=3)       # nprobe == nlist scans every list, so the result is exact
    rng = np.random.default_rng(0)
    vecs = rng.standard_normal((60, 4)).astype(np.float32)[:6]
    expected = [str(1001 + i) for i in np.argsort(-(vecs @ QUERY[0]))]
    assert [p for p, _ in d.search("x", 6)] == expected


def test_ids_cache_written_on_first_use_and_reused(tmp_path):
    cache = tmp_path / "ids.npy"
    d = DenseIndex(flat_index(), encode, ids_cache=cache)
    assert not cache.exists()
    assert d.contains(["111"]) == {"111"}
    assert cache.exists()
    # a new instance over an EMPTY index with the same cache path must answer from the saved ids
    empty = faiss.IndexIDMap(faiss.IndexFlatIP(4))
    d2 = DenseIndex(empty, encode, ids_cache=cache)
    assert d2.contains(["111", "555", "999"]) == {"111", "555"}
