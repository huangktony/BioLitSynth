"""Retrieval-only eval (plan §11.3b): each BioASQ question is one sub-question; only the Query Builder and the
candidate search run. No extraction, so a question costs 1-3 LLM calls.

Per question it reports:
  recall@fetch        relevant PMIDs among the final query's top `retmax_candidates` esearch results
  recall@fetch_draft  the same for the first LLM draft, before refinement (decision D2)
  misses              each missed PMID as "beyond_cap" (the final query matches it, past the fetch cap) or
                      "not_matched" (the final query doesn't match it). D6 Stage 0 splits not_matched further in P3.
Candidates are esearch results, before efetch, so papers without abstracts still count here (recall is slightly
optimistic for what reaches the extractor). Recall@X after rerank arrives with the reranker in P3.
"""
from __future__ import annotations

import asyncio
import json
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..agents import query_builder as qbuild
from ..config import Settings
from ..llm import CallBudget, LLMClient, StageLLM
from ..pubmed import PubMedClient, TokenBucket
from ..schemas import Facet, SubQuestion
from ..store import Database
from ..trace import Tracer
from .bioasq import BioasqQuestion

_CHUNK = 200            # PMIDs per membership probe


@dataclass
class QuestionResult:
    id: str
    question: str
    n_relevant: int
    draft_query: str
    draft_hits: int
    final_query: str
    final_hits: int
    refinements: int
    in_band: bool
    recall_fetch: float
    recall_fetch_draft: float
    misses: dict[str, str] = field(default_factory=dict)      # pmid -> beyond_cap | not_matched


@dataclass
class RetrievalReport:
    eval_id: str
    split: str
    retmax: int
    model: str
    prompt_version: str
    questions: list[QuestionResult]
    llm_calls: int
    ncbi_calls: int

    def summary(self) -> dict:
        qs = self.questions
        mean = (lambda xs: round(statistics.fmean(xs), 4) if xs else None)
        misses = [m for q in qs for m in q.misses.values()]
        return {"questions": len(qs), "mean_recall_fetch": mean([q.recall_fetch for q in qs]),
                "mean_recall_fetch_draft": mean([q.recall_fetch_draft for q in qs]),
                "share_in_band": mean([float(q.in_band) for q in qs]),
                "mean_refinements": mean([q.refinements for q in qs]),
                "misses_beyond_cap": misses.count("beyond_cap"), "misses_not_matched": misses.count("not_matched"),
                "llm_calls": self.llm_calls, "ncbi_calls": self.ncbi_calls}


def _recall(relevant: tuple[str, ...], found: list[str]) -> float:
    return len(set(relevant) & set(found)) / len(relevant) if relevant else 0.0


async def matched_by(query: str, pmids: list[str], pubmed: PubMedClient, *, parent: str | None = None) -> set[str]:
    """Which of `pmids` the query matches, without downloading its full result list (plan §11.5, D6)."""
    hit: set[str] = set()
    for i in range(0, len(pmids), _CHUNK):
        chunk = pmids[i:i + _CHUNK]
        probe = f"({query}) AND ({' OR '.join(f'{p}[pmid]' for p in chunk)})"
        hit |= set((await pubmed.search(probe, retmax=len(chunk), stage="query", parent=parent)).pmids)
    return hit


async def evaluate_question(q: BioasqQuestion, llm: StageLLM, pubmed: PubMedClient, settings: Settings, *,
                            parent: str | None = None) -> QuestionResult:
    band, retmax = settings.query_band, settings.limits.retmax_candidates
    sq = SubQuestion(id="sq1", text=q.body, facet=Facet.OTHER, rationale="BioASQ question", key_concepts=[])
    built = await qbuild.build_query(sq, llm, pubmed, band, max_refinements=settings.limits.query_refinement_attempts,
                                     version=settings.prompt_versions.get(qbuild.PROMPT), parent=parent)
    fetched = (await pubmed.search(built.query_string, retmax=retmax, parent=parent)).pmids
    draft_q, draft_hits = built.history[0]
    draft_fetched = fetched if draft_q == built.query_string else \
        (await pubmed.search(draft_q, retmax=retmax, parent=parent)).pmids
    missed = [p for p in q.relevant_pmids if p not in set(fetched)]
    matched = await matched_by(built.query_string, missed, pubmed, parent=parent) if missed else set()
    return QuestionResult(
        id=q.id, question=q.body, n_relevant=len(q.relevant_pmids), draft_query=draft_q, draft_hits=draft_hits,
        final_query=built.query_string, final_hits=built.hit_count, refinements=len(built.history) - 1,
        in_band=qbuild.in_band(built.hit_count, band), recall_fetch=_recall(q.relevant_pmids, fetched),
        recall_fetch_draft=_recall(q.relevant_pmids, draft_fetched),
        misses={p: ("beyond_cap" if p in matched else "not_matched") for p in missed})


async def run_retrieval_eval(questions: list[BioasqQuestion], settings: Settings, *, split: str,
                             client: LLMClient, max_llm_calls: int | None = 200, transport=None, ncbi_env=None,
                             llm_limiter: TokenBucket | None = None, ncbi_limiter: TokenBucket | None = None,
                             out_dir: str | Path | None = None) -> tuple[RetrievalReport, Path]:
    eval_id = f"eval_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_retrieval_{split}"
    data_dir = settings.paths.data_dir
    db = Database(data_dir / "biolitsynth.db")
    pubmed = None
    try:
        db.create_run(eval_id, f"(retrieval eval, {split}, {len(questions)} BioASQ questions)", settings)
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
                r = await evaluate_question(q, llm, pubmed, settings, parent=sp.span_id)
                sp.message = f"recall@fetch {r.recall_fetch:.2f} (draft {r.recall_fetch_draft:.2f})"
            results.append(r)
        report = RetrievalReport(eval_id, split, settings.limits.retmax_candidates, ref.model,
                                 qbuild.load_prompt(qbuild.PROMPT, version=settings.prompt_versions.get(qbuild.PROMPT)).version, results, llm.calls, pubmed.network_calls)
        out = Path(out_dir or settings.paths.runs_dir / eval_id)
        out.mkdir(parents=True, exist_ok=True)
        path = out / "retrieval.json"
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
