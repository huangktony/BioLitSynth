"""MedCPT cross-encoder reranker (plan §5.4c). Local model, no API calls. torch/transformers load lazily."""
from __future__ import annotations

import hashlib
from typing import Mapping, Protocol, Sequence

from ..config import RerankConfig
from ..schemas import Paper, RelevanceScore, SubQuestion
from ..store import Database


class Reranker(Protocol):
    model: str

    def score(self, query: str, docs: Sequence[tuple[str, str]]) -> list[float]:
        """docs = (pmid, article_text). One raw score per doc, same order."""
        ...


def resolve_device(name: str = "auto") -> str:
    if name != "auto":
        return name
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else "cpu"


class CrossEncoderReranker:
    def __init__(self, cfg: RerankConfig):
        self.cfg = cfg
        self.model = cfg.model
        self.device: str | None = None
        self._tok = None
        self._net = None

    def _load(self) -> None:
        import torch  # noqa: F401  (import before transformers so a missing torch fails clearly)
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.device = resolve_device(self.cfg.device)
        self._tok = AutoTokenizer.from_pretrained(self.cfg.model)
        self._net = AutoModelForSequenceClassification.from_pretrained(self.cfg.model).to(self.device).eval()

    def score(self, query: str, docs: Sequence[tuple[str, str]]) -> list[float]:
        if not docs:
            return []
        import torch
        if self._net is None:
            self._load()
        out: list[float] = []
        bs = self.cfg.batch_size
        with torch.no_grad():
            for i in range(0, len(docs), bs):
                pairs = [[query, text] for _, text in docs[i:i + bs]]
                enc = self._tok(pairs, truncation=True, padding=True, max_length=self.cfg.max_length,
                                return_tensors="pt").to(self.device)
                out.extend(float(x) for x in self._net(**enc).logits.squeeze(-1).reshape(-1).cpu().tolist())
        return out


class FakeReranker:
    """Test double: scores come from a pmid -> score map."""

    def __init__(self, scores: Mapping[str, float], *, default: float = 0.0, model: str = "fake-reranker"):
        self._scores = dict(scores)
        self._default = default
        self.model = model
        self.calls = 0

    def score(self, query: str, docs: Sequence[tuple[str, str]]) -> list[float]:
        self.calls += len(docs)
        return [self._scores.get(pmid, self._default) for pmid, _ in docs]


def query_text(sq: SubQuestion) -> str:
    return f"{sq.text} {'; '.join(sq.key_concepts)}" if sq.key_concepts else sq.text


def article_text(p: Paper) -> str:
    return f"{p.title} {p.abstract}".strip()


def select_top(scored: Sequence[tuple[str, float]], *, sub_question_id: str, top_x: int, min_score: float | None,
               model: str, pubmed_rank: Mapping[str, int]) -> list[RelevanceScore]:
    ranked = sorted(scored, key=lambda t: (-t[1], pubmed_rank[t[0]], t[0]))
    out = []
    for rank, (pmid, score) in enumerate(ranked, start=1):
        below_min = min_score is not None and score < min_score
        kept = rank <= top_x and not below_min
        reason = None if kept else ("below_min_score" if below_min else "below_top_x")
        out.append(RelevanceScore(sub_question_id=sub_question_id, pmid=pmid, score=score, rank=rank,
                                  pubmed_rank=pubmed_rank[pmid], kept=kept, drop_reason=reason, model=model))
    return out


def score_with_cache(reranker: Reranker, db: Database, query: str, docs: Sequence[tuple[str, str]]) -> dict[str, float]:
    sha = hashlib.sha256(query.encode("utf-8")).hexdigest()
    got = db.get_rerank_scores(reranker.model, sha, [p for p, _ in docs])
    misses = [(p, t) for p, t in docs if p not in got]
    if misses:
        new = list(zip((p for p, _ in misses), reranker.score(query, misses)))
        db.put_rerank_scores(reranker.model, sha, new)
        got.update(new)
    return {p: got[p] for p, _ in docs}
