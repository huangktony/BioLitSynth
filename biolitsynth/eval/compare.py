"""Retrieval head-to-head (docs/PROPOSAL_RETRIEVAL_COMPARISON.md): Boolean deep vs full-corpus MedCPT vs hybrid.

Per BioASQ question (one sub-question) the Query Builder writes a query, then both retrievers run once, every
fetched paper is scored once by the cross-encoder, and seven options are cut from those same lists:
  boolean_order_25 / dense_order_25   raw top 25 of each list, no rerank (the baselines)
  boolean_500 / boolean_1000          Boolean top N by PubMed order -> rerank -> top 25
  dense_200 / dense_1000              dense top N -> rerank -> top 25
  hybrid                              Boolean top 500 + dense top 200 not already in it -> rerank -> top 25
Papers PubMed cannot return (no abstract) drop out of the reranked options but not out of the raw pools.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..agents import query_builder as qbuild
from ..config import Settings
from ..llm import CallBudget, LLMClient, StageLLM
from ..pubmed import PubMedClient, TokenBucket
from ..rerank import Reranker, article_text, query_text, score_with_cache
from ..retrieval import DenseIndex
from ..schemas import Facet, SubQuestion
from ..store import Database
from ..trace import Tracer
from .bioasq import BioasqQuestion

OPTIONS = ("boolean_order_25", "boolean_500", "boolean_1000", "dense_order_25", "dense_200", "dense_1000", "hybrid")
TOP = 25


@dataclass
class OptionResult:
    pool_size: int
    pool_recall: float
    recall_at_25: float
    precision_at_25: float
    top25: list[str]


@dataclass
class CompareQuestionResult:
    id: str
    question: str
    n_relevant: int
    final_query: str
    final_hits: int
    n_boolean: int
    n_dense: int
    n_fetched: int
    relevant_in_index: int
    hybrid_only_boolean: int
    hybrid_only_dense: int
    options: dict[str, OptionResult]
    dense_s: float
    fetch_s: float
    rerank_s: float
    rerank_pairs: int


@dataclass
class CompareReport:
    eval_id: str
    split: str
    model: str
    prompt_version: str
    reranker: str
    nprobe: int | None
    questions: list[CompareQuestionResult]
    llm_calls: int
    ncbi_calls: int

    def summary(self) -> dict:
        qs = self.questions
        mean = (lambda xs: round(statistics.fmean(xs), 4) if xs else None)
        options = {o: {"recall_at_25": mean([q.options[o].recall_at_25 for q in qs]),
                       "precision_at_25": mean([q.options[o].precision_at_25 for q in qs]),
                       "pool_recall": mean([q.options[o].pool_recall for q in qs])} for o in OPTIONS}
        pairs = sum(q.rerank_pairs for q in qs)
        rerank_s = sum(q.rerank_s for q in qs)
        pps = pairs / rerank_s if rerank_s else None
        mean_dense, mean_fetch = mean([q.dense_s for q in qs]), mean([q.fetch_s for q in qs])
        est = 3 * (mean_dense + mean_fetch + 700 / pps) if pps and qs else None
        return {"questions": len(qs), "options": options,
                "mean_relevant_in_index": mean([q.relevant_in_index / q.n_relevant for q in qs if q.n_relevant]),
                "hybrid_only_boolean": sum(q.hybrid_only_boolean for q in qs),
                "hybrid_only_dense": sum(q.hybrid_only_dense for q in qs),
                "rerank_pairs": pairs, "pairs_per_second": None if pps is None else round(pps, 2),
                "mean_dense_s": mean_dense, "mean_fetch_s": mean_fetch,
                "est_hybrid_3_subq_s": None if est is None else round(est, 1),
                "llm_calls": self.llm_calls, "ncbi_calls": self.ncbi_calls}


def _option(pool: list[str], relevant: set[str], top25: list[str]) -> OptionResult:
    if not relevant:
        return OptionResult(len(pool), 0.0, 0.0, 0.0, top25)
    hit = len(relevant & set(top25))
    return OptionResult(len(pool), len(relevant & set(pool)) / len(relevant), hit / len(relevant), hit / TOP, top25)


def _reranked_top(pool: list[str], fetched: set[str], scores: dict[str, float]) -> list[str]:
    kept = [(p, i) for i, p in enumerate(pool) if p in fetched]
    kept.sort(key=lambda t: (-scores[t[0]], t[1]))
    return [p for p, _ in kept[:TOP]]


async def compare_question(q: BioasqQuestion, llm: StageLLM, pubmed: PubMedClient, dense: DenseIndex,
                           reranker: Reranker, db: Database, settings: Settings, *,
                           parent: str | None = None) -> CompareQuestionResult:
    tracer = pubmed.tracer
    band = settings.query_band
    sq = SubQuestion(id="sq1", text=q.body, facet=Facet.OTHER, rationale="BioASQ question", key_concepts=[])
    built = await qbuild.build_query(sq, llm, pubmed, band, max_refinements=settings.limits.query_refinement_attempts,
                                     version=settings.prompt_versions.get(qbuild.PROMPT), parent=parent)
    boolean = (await pubmed.search(built.query_string, retmax=1000, parent=parent)).pmids

    async def timed_dense() -> tuple[list[str], float]:
        t = time.perf_counter()
        if tracer:
            async with tracer.span("fetch", "dense_search", parent=parent) as sp:
                hits = dense.search(q.body, 1000)
                sp.message = f"{len(hits)} dense hits"
        else:
            hits = dense.search(q.body, 1000)
        return [p for p, _ in hits], time.perf_counter() - t

    dense_list, dense_s = await timed_dense()

    union = list(dict.fromkeys([*boolean, *dense_list]))
    t = time.perf_counter()
    res = await pubmed.fetch(union, parent=parent)
    fetch_s = time.perf_counter() - t
    fetched = {p.pmid: p for p in res.papers}
    for p in res.papers:
        db.upsert_paper(p)

    qtext = query_text(sq)
    docs = [(pmid, article_text(p)) for pmid, p in fetched.items()]
    sha = hashlib.sha256(qtext.encode("utf-8")).hexdigest()
    cached = db.get_rerank_scores(reranker.model, sha, [p for p, _ in docs])
    pairs = len(docs) - len(cached)
    t = time.perf_counter()
    if tracer:
        async with tracer.span("rerank", "cross_encoder", parent=parent, input_refs=[f"bioasq:{q.id}"]) as sp:
            scores = score_with_cache(reranker, db, qtext, docs)
            sp.message = f"{pairs} pairs scored, {len(docs) - pairs} cached"
    else:
        scores = score_with_cache(reranker, db, qtext, docs)
    rerank_s = time.perf_counter() - t

    b500, d200 = boolean[:500], dense_list[:200]
    pools = {"boolean_order_25": boolean[:25], "boolean_500": b500, "boolean_1000": boolean[:1000],
             "dense_order_25": dense_list[:25], "dense_200": d200, "dense_1000": dense_list[:1000],
             "hybrid": b500 + [p for p in d200 if p not in set(b500)]}
    relevant = set(q.relevant_pmids)
    options = {name: _option(pool, relevant,
                             pool if name.endswith("_order_25") else _reranked_top(pool, set(fetched), scores))
               for name, pool in pools.items()}
    return CompareQuestionResult(
        id=q.id, question=q.body, n_relevant=len(relevant), final_query=built.query_string,
        final_hits=built.hit_count, n_boolean=len(boolean), n_dense=len(dense_list), n_fetched=len(fetched),
        relevant_in_index=len(dense.contains(relevant)),
        hybrid_only_boolean=len(relevant & (set(b500) - set(d200))),
        hybrid_only_dense=len(relevant & (set(d200) - set(b500))),
        options=options, dense_s=dense_s, fetch_s=fetch_s, rerank_s=rerank_s, rerank_pairs=pairs)


async def run_compare_eval(questions: list[BioasqQuestion], settings: Settings, *, split: str, client: LLMClient,
                           reranker: Reranker, dense: DenseIndex, max_llm_calls: int | None = 300, transport=None,
                           ncbi_env=None, llm_limiter: TokenBucket | None = None,
                           ncbi_limiter: TokenBucket | None = None,
                           out_dir: str | Path | None = None) -> tuple[CompareReport, Path]:
    eval_id = f"eval_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_compare_{split}"
    data_dir = settings.paths.data_dir
    db = Database(data_dir / "biolitsynth.db")
    pubmed = None
    try:
        db.create_run(eval_id, f"(compare eval, {split}, {len(questions)} BioASQ questions)", settings)
        tracer = Tracer(db, eval_id)
        ref = settings.models.query
        llm = StageLLM(client, model=ref.model, stage="query", agent="query_builder", db=db, tracer=tracer,
                       limiter=llm_limiter or TokenBucket(getattr(settings.providers, ref.provider).rpm / 60),
                       semaphore=asyncio.Semaphore(settings.concurrency.llm), budget=CallBudget(max_llm_calls))
        pubmed = PubMedClient(settings.ncbi, db, data_dir / "cache" / "pubmed", tracer=tracer,
                              concurrency=settings.concurrency.ncbi, transport=transport, env=ncbi_env,
                              limiter=ncbi_limiter)
        results = []
        for q in questions:
            async with tracer.span("query", "eval", input_refs=[f"bioasq:{q.id}"]) as sp:
                r = await compare_question(q, llm, pubmed, dense, reranker, db, settings, parent=sp.span_id)
                sp.message = f"hybrid r@25 {r.options['hybrid'].recall_at_25:.2f}, " \
                             f"boolean_500 {r.options['boolean_500'].recall_at_25:.2f}"
            results.append(r)
        version = qbuild.load_prompt(qbuild.PROMPT, version=settings.prompt_versions.get(qbuild.PROMPT)).version
        report = CompareReport(eval_id, split, ref.model, version, reranker.model, dense.nprobe, results,
                               llm.calls, pubmed.network_calls)
        out = Path(out_dir or settings.paths.runs_dir / eval_id)
        out.mkdir(parents=True, exist_ok=True)
        path = out / "compare.json"
        path.write_text(json.dumps({"summary": report.summary(), **asdict(report)}, indent=2) + "\n")
        db.set_run_status(eval_id, "completed_eval")
        db.export_trace_jsonl(eval_id, out / "trace.jsonl")
        return report, path
    except BaseException:
        db.set_run_status(eval_id, "failed")
        raise
    finally:
        if pubmed:
            await pubmed.aclose()
        db.close()
