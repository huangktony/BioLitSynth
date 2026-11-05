"""NCBI E-utilities client (plan §5.3, §6.3).

Every network call goes through one shared rate limiter and emits a trace span. Two caches keep reruns free:
raw per-PMID XML on disk (never refetched) and esearch results in SQLite (7-day TTL from config).
Secrets come from environment variables named in config; they are never logged or put in a span.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Mapping

import httpx

from ..config import NcbiConfig
from ..schemas import Paper, Stage
from ..store import Database
from ..trace import SpanHandle, Tracer
from .parser import ParseResult, SkippedArticle, parse_pubmed_xml, split_articles
from .ratelimit import TokenBucket

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


class PubMedError(RuntimeError):
    pass


class QueryRejected(PubMedError):
    """esearch answered, but refused the query string (bad syntax). Network and HTTP failures are not this."""


@dataclass
class SearchResult:
    total: int                   # hits PubMed reports for the query
    pmids: list[str]             # ids returned, in PubMed's order (rank = index + 1)
    cached: bool


@dataclass
class FetchResult:
    papers: list[Paper] = field(default_factory=list)       # with abstracts, in request order
    skipped: list[SkippedArticle] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)        # requested, but NCBI returned no record
    cache_hits: int = 0
    fetched: int = 0


@dataclass
class QueryFetch:
    search: SearchResult
    fetch: FetchResult

    def ranked(self) -> list[tuple[str, int]]:
        """(pmid, rank) for every stored paper, using the rank PubMed gave it in the search."""
        rank = {p: i + 1 for i, p in enumerate(self.search.pmids)}
        return [(p.pmid, rank[p.pmid]) for p in self.fetch.papers if p.pmid in rank]


class PubMedClient:
    def __init__(self, cfg: NcbiConfig, db: Database, cache_dir: str | Path, *, tracer: Tracer | None = None,
                 concurrency: int = 5, transport: httpx.AsyncBaseTransport | None = None,
                 env: Mapping[str, str] | None = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, limiter: TokenBucket | None = None):
        env = os.environ if env is None else env
        self.cfg, self.db, self.tracer = cfg, db, tracer
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._api_key = env.get(cfg.api_key_env) or None
        self._email = env.get(cfg.email_env) or None
        self._limiter = limiter or TokenBucket(10 if self._api_key else 3)
        self._sem = asyncio.Semaphore(concurrency)
        self._http = httpx.AsyncClient(base_url=BASE_URL, transport=transport, timeout=cfg.timeout_s)
        self._clock, self._sleep = clock, sleep
        self.network_calls = 0

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- tracing -------------------------------------------------------------------------------
    @asynccontextmanager
    async def _span(self, stage: Stage, label: str, refs: list[str], parent: str | None) -> AsyncIterator[SpanHandle]:
        if self.tracer is None:
            yield SpanHandle("untraced", parent)
            return
        async with self.tracer.span(stage, "pubmed_client", input_refs=refs, parent=parent) as sp:
            sp.message = label
            yield sp

    # --- HTTP ----------------------------------------------------------------------------------
    def _base_params(self) -> dict[str, str]:
        p = {"db": "pubmed", "tool": self.cfg.tool}
        if self._email:
            p["email"] = self._email
        if self._api_key:
            p["api_key"] = self._api_key
        return p

    async def _request(self, method: str, endpoint: str, params: dict[str, str], stage: Stage, label: str,
                       parent: str | None) -> httpx.Response:
        async with self._sem, self._span(stage, f"{method} {endpoint}: {label}", [], parent) as sp:
            last: Exception | None = None
            for attempt in range(self.cfg.max_retries + 1):
                await self._limiter.acquire()
                self.network_calls += 1
                try:
                    kw = {"params": params} if method == "GET" else {"data": params}
                    resp = await self._http.request(method, endpoint, **kw)
                except httpx.TransportError as e:
                    last = e
                else:
                    if resp.status_code == 429 or resp.status_code >= 500:
                        last = PubMedError(f"HTTP {resp.status_code}")
                    elif resp.status_code >= 400:
                        raise PubMedError(f"{endpoint}: HTTP {resp.status_code}")
                    else:
                        return resp
                sp.message = f"{method} {endpoint}: {label} (retry {attempt + 1}: {type(last).__name__})"
                if attempt < self.cfg.max_retries:
                    await self._sleep(min(2 ** attempt, 8))
            raise PubMedError(f"{endpoint} failed after {self.cfg.max_retries + 1} attempts: {last}")

    # --- esearch -------------------------------------------------------------------------------
    async def search(self, query: str, *, retmax: int = 100, sort: str = "relevance",
                     stage: Stage = "fetch", parent: str | None = None) -> SearchResult:
        key = hashlib.sha256(f"{query}\x00{retmax}\x00{sort}".encode()).hexdigest()
        async with self._span(stage, f"esearch: {query[:80]}", [], parent) as sp:
            hit = self.db.get_esearch(key, max_age_s=self.cfg.esearch_ttl_days * 86400, now=self._clock())
            if hit:
                sp.cache_hit = True
                sp.output_refs = [f"pmid:{p}" for p in hit[1]]
                return SearchResult(hit[0], hit[1], cached=True)
            resp = await self._request("GET", "esearch.fcgi",
                                       {**self._base_params(), "term": query, "retmax": str(retmax), "sort": sort,
                                        "retmode": "json"}, stage, query[:80], sp.span_id)
            data = resp.json()
            res = data.get("esearchresult") or {}
            if "error" in data or "ERROR" in res or "count" not in res:
                raise QueryRejected(f"esearch rejected the query: {data.get('error') or res.get('ERROR') or data}")
            total, pmids = int(res["count"]), list(res.get("idlist", []))
            self.db.put_esearch(key, query, retmax, sort, total, pmids, self._clock())
            sp.output_refs = [f"pmid:{p}" for p in pmids]
            return SearchResult(total, pmids, cached=False)

    async def count(self, query: str, *, parent: str | None = None) -> int:
        """Hit count only (Query Builder's `pubmed_count` tool, plan §5.2)."""
        return (await self.search(query, retmax=0, stage="query", parent=parent)).total

    # --- efetch --------------------------------------------------------------------------------
    def _cache_path(self, pmid: str) -> Path:
        return self.cache_dir / f"{pmid}.xml"

    async def fetch(self, pmids: list[str], *, parent: str | None = None) -> FetchResult:
        pmids = list(dict.fromkeys(pmids))
        misses = [p for p in pmids if not self._cache_path(p).exists()]
        res = FetchResult(cache_hits=len(pmids) - len(misses))
        async with self._span("fetch", f"efetch: {len(pmids)} ids, {len(misses)} to download",
                              [f"pmid:{p}" for p in pmids], parent) as sp:
            sp.cache_hit = not misses
            size = self.cfg.efetch_batch_size
            for i in range(0, len(misses), size):
                batch = misses[i:i + size]
                resp = await self._request("POST", "efetch.fcgi",
                                           {**self._base_params(), "id": ",".join(batch), "rettype": "abstract",
                                            "retmode": "xml"}, "fetch", f"{len(batch)} ids", sp.span_id)
                articles = split_articles(resp.content)
                if not articles and b"<ERROR>" in resp.content:
                    raise PubMedError(f"efetch returned an error document for ids {batch[:3]}...")
                for pmid, doc in articles.items():
                    tmp = self._cache_path(pmid).with_suffix(".tmp")
                    tmp.write_bytes(doc)
                    tmp.replace(self._cache_path(pmid))         # atomic: a crash never leaves half a file
                res.fetched += len(articles)
            parsed: dict[str, Paper] = {}
            for pmid in pmids:
                path = self._cache_path(pmid)
                if not path.exists():
                    res.missing.append(pmid)
                    continue
                one: ParseResult = parse_pubmed_xml(path.read_bytes())
                res.skipped += one.skipped
                parsed.update({p.pmid: p for p in one.papers})
            res.papers = [parsed[p] for p in pmids if p in parsed]
            sp.output_refs = [f"pmid:{p.pmid}" for p in res.papers]
            return res

    async def search_and_fetch(self, query: str, *, retmax: int = 100, parent: str | None = None) -> QueryFetch:
        s = await self.search(query, retmax=retmax, parent=parent)
        return QueryFetch(s, await self.fetch(s.pmids, parent=parent))
