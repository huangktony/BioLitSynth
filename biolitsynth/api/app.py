"""Dashboard API over the SQLite store: the GET endpoints of plan §16.3, plus two ways to start work.

- POST /api/runs {question}: a live run through `graph.runner.run_live` (Lead plan, LLM Query Builder, fetch,
  dedup; it stops there until P3). Same code path as `biolitsynth run "<question>"`.
- POST /api/fetch {query}: the P1 fetch demo for one hand-written PubMed query.
These are the only endpoints that write, and only through those two functions. One job at a time: free-tier LLM
quota and NCBI rate limits are per user. SSE progress and query approval arrive in P7a; the page in static/ is a
stopgap that P7b's React app replaces.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from importlib import resources
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..config import Settings
from ..eval.bioasq import load_bioasq, load_subset
from ..eval.log import DEFAULT_LOG, append_log, read_log, record_from_report, run_config_json
from ..eval.retrieval import run_retrieval_eval
from ..graph.runner import resolve_clients, run_live
from ..llm import BudgetExceeded, MissingApiKey, ModelNotVerified
from ..pubmed.demo import fetch_demo
from ..store import Database

# Display order for the pipeline panel (graph order, plan §3).
STAGE_ORDER = ["plan", "query", "approve_queries", "fetch", "dedup", "rerank", "extract", "coverage", "cluster",
               "normalize_terms", "conflict", "synthesize", "cite", "check", "critique", "route", "assemble"]


class FetchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=1000)   # PubMed syntax, e.g. "metformin AND colorectal neoplasms[mh]"
    retmax: int = Field(25, ge=1, le=200)


class EvalRequest(BaseModel):
    limit: int = Field(5, ge=1, le=30)   # first N dev questions. No split field: held-back is never run from the web UI


class RunRequest(BaseModel):
    question: str = Field(min_length=15, max_length=1000)   # plan §16.3


def create_app(settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None,
               live_overrides: dict | None = None,
               eval_overrides: dict | None = None) -> FastAPI:
    """`transport` swaps in a fake NCBI and `live_overrides` (clients, limiters, ...) go to run_live; both for tests."""
    db = Database(settings.paths.data_dir / "biolitsynth.db")
    app = FastAPI(title="BioLitSynth dashboard")
    app.state.db = db
    app.router.on_shutdown.append(db.close)
    ev = dict(eval_overrides or {})
    bioasq_path = Path(ev.pop("bioasq_path", None) or settings.paths.data_dir / "eval" / "bioasq" / "training14b.json")
    subset_path = Path(ev.pop("subset_path", None) or "tests/golden/bioasq_subset.yaml")
    log_path = Path(ev.pop("log_path", None) or DEFAULT_LOG)
    job_lock = asyncio.Lock()     # one run or fetch at a time (shared LLM quota and NCBI rate limit)

    def run_or_404(run_id: str):
        row = db.get_run(run_id)
        if row is None:
            raise HTTPException(404, f"no run {run_id}")
        return row

    def counts(run_id: str) -> dict:
        one = lambda sql: db.query(sql, (run_id,))[0][0]  # noqa: E731
        return {
            "sub_questions": one("SELECT COUNT(*) FROM sub_questions WHERE run_id = ?"),
            "queries": one("SELECT COUNT(*) FROM queries WHERE run_id = ?"),
            "papers": one("SELECT COUNT(DISTINCT pmid) FROM run_papers WHERE run_id = ?"),
            "evidence": one("SELECT COUNT(*) FROM evidence WHERE run_id = ?"),
            "claims": one("SELECT COUNT(DISTINCT id) FROM claims WHERE run_id = ?"),
            "gap_rounds": one("SELECT COALESCE(MAX(round), 0) FROM queries WHERE run_id = ?"),
            "synth_attempts": one("SELECT COALESCE(MAX(attempt) + 1, 0) FROM claims WHERE run_id = ?"),
            "trace_events": one("SELECT COUNT(*) FROM trace_events WHERE run_id = ?"),
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return resources.files("biolitsynth.api").joinpath("static/index.html").read_text()

    @app.post("/api/runs")
    async def start_run(req: RunRequest) -> dict:
        if job_lock.locked():
            raise HTTPException(409, "a run or fetch is already in progress; wait for it to finish")
        async with job_lock:
            try:
                lv = await run_live(req.question.strip(), None, settings, transport=transport, **(live_overrides or {}))
            except (MissingApiKey, ModelNotVerified) as exc:    # raised before any run row is written
                raise HTTPException(400, f"cannot start: {exc}") from exc
            except BudgetExceeded as exc:
                raise HTTPException(429, f"LLM call budget used up: {exc}") from exc
            except Exception as exc:                            # run_live marks the run failed
                raise HTTPException(502, f"run failed: {type(exc).__name__}: {exc}") from exc
        return {"run_id": lv.run_id, "papers_kept": lv.papers_kept, "papers_dropped": lv.papers_dropped,
                "llm_calls": lv.llm_calls, "llm_cache_hits": lv.llm_cache_hits, "network_calls": lv.network_calls}

    @app.post("/api/fetch")
    async def fetch(req: FetchRequest) -> dict:
        if job_lock.locked():
            raise HTTPException(409, "a run or fetch is already in progress; wait for it to finish")
        async with job_lock:
            try:
                summary = await fetch_demo(req.query.strip(), settings, retmax=req.retmax, transport=transport)
            except Exception as exc:  # the run row is marked failed by fetch_demo; show the reason
                raise HTTPException(502, f"PubMed fetch failed: {type(exc).__name__}: {exc}") from exc
        return asdict(summary)

    @app.get("/api/runs")
    def list_runs() -> list[dict]:
        rows = db.query("SELECT run_id, question, status, created_at FROM runs WHERE run_id NOT LIKE 'eval\\_%' ESCAPE '\\' "
                        "ORDER BY created_at DESC")
        return [{**dict(r), "counts": counts(r["run_id"])} for r in rows]

    def eval_report(eval_id: str) -> dict | None:
        path = settings.paths.runs_dir / eval_id / "retrieval.json"
        return json.loads(path.read_text()) if path.exists() else None

    @app.get("/api/eval/subset")
    def eval_subset() -> dict:
        out = {"source_present": bioasq_path.exists(), "subset_present": subset_path.exists(),
               "reviewed_by": None, "dev": 0, "held_back": 0}
        if out["subset_present"]:
            sub = load_subset(subset_path) or {}
            out.update(reviewed_by=sub.get("reviewed_by"), dev=len(sub.get("dev") or []),
                       held_back=len(sub.get("held_back") or []))
        return out

    @app.get("/api/eval/log")
    def eval_log() -> list[dict]:
        return sorted(read_log(log_path), key=lambda r: (r.get("date") or "", r["eval_id"]), reverse=True)

    @app.get("/api/eval/questions")
    def eval_questions(ids: str = "") -> dict:
        """Question text for the given ids, from the local BioASQ file only. Never written to the committed log."""
        if not bioasq_path.exists():
            return {}
        qs = load_bioasq(bioasq_path)
        return {i: qs[i].body for i in ids.split(",") if i in qs}

    @app.get("/api/evals")
    def list_evals() -> list[dict]:
        rows = db.query("SELECT run_id, question, status, created_at FROM runs WHERE run_id LIKE 'eval\\_%' ESCAPE '\\' "
                        "ORDER BY created_at DESC, run_id DESC")
        return [{**dict(r), "summary": (rep or {}).get("summary")} for r in rows
                for rep in [eval_report(r["run_id"])]]

    @app.get("/api/evals/{eval_id}")
    def eval_detail(eval_id: str) -> dict:
        r = db.get_run(eval_id)
        if r is None or not eval_id.startswith("eval_"):
            raise HTTPException(404, f"no eval {eval_id}")
        return {"run_id": r["run_id"], "status": r["status"], "created_at": r["created_at"],
                "report": eval_report(eval_id)}

    @app.post("/api/evals")
    async def start_eval(req: EvalRequest) -> dict:
        """Retrieval eval on the first `limit` dev questions (plan §11.3b). Never the held-back split (§11.5 rule 2)."""
        if job_lock.locked():
            raise HTTPException(409, "a run or fetch is already in progress; wait for it to finish")
        async with job_lock:
            if not bioasq_path.exists():
                raise HTTPException(400, f"BioASQ source file not found: {bioasq_path}")
            if not subset_path.exists():
                raise HTTPException(400, f"subset file not found: {subset_path}. Create it with: "
                                         f"biolitsynth eval select --bioasq {bioasq_path}")
            questions = load_bioasq(bioasq_path)
            ids = (load_subset(subset_path).get("dev") or [])[: req.limit]
            missing = [i for i in ids if i not in questions]
            if missing:
                raise HTTPException(400, f"{len(missing)} subset ids are not in {bioasq_path}, e.g. {missing[:3]}")
            try:
                client = ev.get("client") or resolve_clients(settings, roles=("query",))["query"]
            except (MissingApiKey, ModelNotVerified) as exc:      # before any run row is written
                raise HTTPException(400, f"cannot start: {exc}") from exc
            extra = {k: ev[k] for k in ("transport", "ncbi_env", "llm_limiter", "ncbi_limiter") if k in ev}
            extra.setdefault("transport", transport)
            try:
                report, _ = await run_retrieval_eval([questions[i] for i in ids], settings, split="dev",
                                                     client=client, max_llm_calls=3 * req.limit, **extra)
            except Exception as exc:                              # run_retrieval_eval marks the run failed
                raise HTTPException(502, f"eval failed: {type(exc).__name__}: {exc}") from exc
            append_log(record_from_report(report, settings, config_json=run_config_json(settings, report.eval_id)),
                       log_path)
        return {"eval_id": report.eval_id, "summary": report.summary()}

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> dict:
        r = run_or_404(run_id)
        events = db.trace_events(run_id)
        stages = []
        for name in STAGE_ORDER:
            evs = [e for e in events if e["stage"] == name]
            if not evs:
                continue
            last = next((e["message"] for e in reversed(evs) if e["message"]), None)
            stages.append({
                "stage": name, "events": len(evs),
                "retries": sum(e["status"] == "retry" for e in evs),
                "failures": sum(e["status"] == "fail" for e in evs),
                "latency_ms": sum(e["latency_ms"] or 0 for e in evs),
                "last_message": last,
            })
        coverage = [{"message": e["message"], "gap_sub_question_ids": json.loads(e["output_refs_json"] or "[]"),
                     "ts": e["ts"]} for e in events if e["stage"] == "coverage"]
        try:
            plan = db.get_plan(run_id)
        except ValueError:            # a plan stored by an older version that no longer validates
            plan = None
        return {
            "run_id": r["run_id"], "question": r["question"], "status": r["status"], "created_at": r["created_at"],
            "plan": plan.model_dump(mode="json") if plan else None,
            "counts": counts(run_id),
            "sub_questions": [dict(s) for s in db.query(
                "SELECT id, text, facet, rationale FROM sub_questions WHERE run_id = ? ORDER BY id", (run_id,))],
            "queries": [{
                "id": q["id"], "sub_question_id": q["sub_question_id"], "query_string": q["query_string"],
                "hit_count": q["hit_count"], "round": q["round"],
                "refinement_history": json.loads(q["refinement_history_json"] or "[]"),
            } for q in db.query("SELECT * FROM queries WHERE run_id = ? ORDER BY CAST(SUBSTR(id, 2) AS INTEGER)",
                                (run_id,))],
            "stages": stages,
            "coverage": coverage,
        }

    @app.get("/api/runs/{run_id}/papers")
    def papers(run_id: str) -> list[dict]:
        run_or_404(run_id)
        rows = db.query(
            "SELECT p.pmid, p.title, p.journal, p.year, p.is_retracted, rp.query_id, rp.rank "
            "FROM run_papers rp JOIN papers p ON p.pmid = rp.pmid WHERE rp.run_id = ? ORDER BY rp.query_id, rp.rank",
            (run_id,))
        out: dict[str, dict] = {}
        for r in rows:
            p = out.setdefault(r["pmid"], {"pmid": r["pmid"], "title": r["title"], "journal": r["journal"],
                                           "year": r["year"], "is_retracted": bool(r["is_retracted"]),
                                           "rank_by_query": {}})
            p["rank_by_query"][r["query_id"]] = r["rank"]
        return list(out.values())

    @app.get("/api/runs/{run_id}/claims")
    def claims(run_id: str) -> list[dict]:
        """Latest attempt of each claim, with its evidence ids and how many attempts it took."""
        run_or_404(run_id)
        rows = db.query(
            "SELECT c.*, (SELECT COUNT(*) FROM claims c2 WHERE c2.run_id = c.run_id AND c2.id = c.id) AS attempts "
            "FROM claims c WHERE c.run_id = ? AND c.attempt = "
            "(SELECT MAX(attempt) FROM claims c3 WHERE c3.run_id = c.run_id AND c3.id = c.id) "
            "ORDER BY CAST(SUBSTR(c.id, 3) AS INTEGER)", (run_id,))
        return [{
            "id": r["id"], "attempt": r["attempt"], "attempts": r["attempts"], "text": r["text"],
            "claim_type": r["claim_type"], "confidence": r["confidence"], "status": r["status"],
            "evidence_ids": [e[0] for e in db.query(
                "SELECT evidence_id FROM claim_evidence WHERE run_id = ? AND claim_id = ? AND attempt = ? "
                "ORDER BY evidence_id", (run_id, r["id"], r["attempt"]))],
        } for r in rows]

    @app.get("/api/runs/{run_id}/trace")
    def trace(run_id: str) -> dict:
        run_or_404(run_id)
        events = []
        for r in db.trace_events(run_id):
            d = dict(r)
            d["input_refs"] = json.loads(d.pop("input_refs_json") or "[]")
            d["output_refs"] = json.loads(d.pop("output_refs_json") or "[]")
            d["cache_hit"] = bool(d["cache_hit"])
            events.append(d)
        return {"events": events, "totals": {
            "events": len(events),
            "llm_calls": sum(e["event_type"] == "llm_call" for e in events),
            "tokens_in": sum(e["tokens_in"] or 0 for e in events),
            "tokens_out": sum(e["tokens_out"] or 0 for e in events),
            "cache_hits": sum(e["cache_hit"] for e in events),    # any span (PubMed cache included)
            "llm_cache_hits": sum(e["cache_hit"] for e in events if e["event_type"] == "llm_call"),
        }}

    return app
