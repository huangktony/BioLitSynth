"""`biolitsynth fetch "<pubmed query>"`: the P1 demo. Fetch, store and dedup one hand-written query."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..config import Settings
from ..graph.runner import new_run_id
from ..store import Database
from ..trace import Tracer
from .client import PubMedClient
from .dedup import dedup_papers


@dataclass
class FetchSummary:
    run_id: str
    total_hits: int
    candidates: int
    stored: int
    kept: int
    skipped_no_abstract: int
    dropped: list[tuple[str, str]]      # (pmid, reason)
    cache_hits: int
    network_calls: int
    search_cached: bool


async def fetch_demo(query: str, settings: Settings, *, retmax: int = 25,
                     transport: httpx.AsyncBaseTransport | None = None) -> FetchSummary:
    data_dir = settings.paths.data_dir
    db, run_id = Database(data_dir / "biolitsynth.db"), new_run_id()
    client = None
    try:
        db.create_run(run_id, f"(fetch demo) {query}", settings)
        client = PubMedClient(settings.ncbi, db, data_dir / "cache" / "pubmed", tracer=Tracer(db, run_id),
                              concurrency=settings.concurrency.ncbi, transport=transport)
        got = await client.search_and_fetch(query, retmax=retmax)
        for paper in got.fetch.papers:
            db.upsert_paper(paper)
        db.add_run_papers(run_id, "q1", got.ranked())
        entries = [(r["pmid"], r["query_id"], r["rank"]) for r in db.run_paper_entries(run_id)]
        result = dedup_papers(entries, db.get_papers(sorted({e[0] for e in entries})))
        db.export_trace_jsonl(run_id, settings.paths.runs_dir / run_id / "trace.jsonl")
        db.set_run_status(run_id, "completed_fetch_demo")
        return FetchSummary(
            run_id=run_id, total_hits=got.search.total, candidates=len(got.search.pmids),
            stored=len(got.fetch.papers), kept=len(result.papers),
            skipped_no_abstract=sum(s.reason == "no_abstract" for s in got.fetch.skipped),
            dropped=[(d.pmid, d.reason) for d in result.dropped], cache_hits=got.fetch.cache_hits,
            network_calls=client.network_calls, search_cached=got.search.cached)
    except BaseException:
        db.set_run_status(run_id, "failed")
        raise
    finally:
        if client:
            await client.aclose()
        db.close()
