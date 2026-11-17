"""Dense retrieval over a prebuilt FAISS index of MedCPT article vectors (Track B comparison only).

Index ids are PMIDs (int64). Vectors are not normalized, and neither are queries (inner product).
faiss/torch/transformers load lazily.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from ..config import DenseConfig


class DenseIndex:
    def __init__(self, index, encode: Callable[[list[str]], np.ndarray], *, nprobe: int | None = None,
                 ids_cache: Path | None = None):
        import faiss
        self.index = index
        self.encode = encode
        self.nprobe = nprobe
        self.ids_cache = Path(ids_cache) if ids_cache else None
        self._ids: np.ndarray | None = None
        if nprobe is not None:
            try:
                faiss.extract_index_ivf(index).nprobe = nprobe
            except RuntimeError:           # not an IVF index (e.g. a flat index in tests)
                pass

    @classmethod
    def from_config(cls, cfg: DenseConfig) -> "DenseIndex":
        import faiss
        index = faiss.read_index(str(cfg.index_path))
        enc = medcpt_query_encoder(cfg.query_encoder, max_length=cfg.max_length)
        return cls(index, enc, nprobe=cfg.nprobe, ids_cache=Path(str(cfg.index_path) + ".pmids.npy"))

    def search(self, text: str, k: int) -> list[tuple[str, float]]:
        vec = np.ascontiguousarray(self.encode([text]), dtype=np.float32)
        scores, ids = self.index.search(vec, k)
        return [(str(int(i)), float(s)) for i, s in zip(ids[0], scores[0]) if i != -1]

    def _all_ids(self) -> np.ndarray:
        if self._ids is not None:
            return self._ids
        if self.ids_cache and self.ids_cache.exists():
            self._ids = np.load(self.ids_cache)
            return self._ids
        import faiss
        try:
            ivf = faiss.extract_index_ivf(self.index)
            il = ivf.invlists
            parts = []
            for li in range(ivf.nlist):
                n = il.list_size(li)
                if n:
                    parts.append(faiss.rev_swig_ptr(il.get_ids(li), n).copy())
            ids = np.concatenate(parts) if parts else np.empty(0, dtype=np.int64)
        except RuntimeError:
            ids = faiss.vector_to_array(self.index.id_map)
        self._ids = np.sort(ids.astype(np.int64))
        if self.ids_cache:
            np.save(self.ids_cache, self._ids)
        return self._ids

    def contains(self, pmids: Iterable[str]) -> set[str]:
        want = list(pmids)
        ids = self._all_ids()
        if not want or not len(ids):
            return set()
        arr = np.array([int(p) for p in want], dtype=np.int64)
        pos = np.searchsorted(ids, arr).clip(max=len(ids) - 1)
        return {p for p, ok in zip(want, ids[pos] == arr) if ok}


def medcpt_query_encoder(model_name: str, *, max_length: int = 64,
                         device: str = "auto") -> Callable[[list[str]], np.ndarray]:
    """texts -> float32 (n, d): the CLS vector of the last hidden state. Model loads on first call."""
    state: dict = {}

    def encode(texts: list[str]) -> np.ndarray:
        import torch
        if not state:
            from transformers import AutoModel, AutoTokenizer
            from ..rerank import resolve_device
            state["dev"] = resolve_device(device)
            state["tok"] = AutoTokenizer.from_pretrained(model_name)
            state["net"] = AutoModel.from_pretrained(model_name).to(state["dev"]).eval()
        with torch.no_grad():
            enc = state["tok"](texts, truncation=True, padding=True, max_length=max_length,
                               return_tensors="pt").to(state["dev"])
            return state["net"](**enc).last_hidden_state[:, 0, :].cpu().numpy().astype(np.float32)

    return encode
