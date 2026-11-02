"""Read-only dashboard API over the SQLite store (a slice of plan §16.3: the GET endpoints only).

No run creation, SSE, or approval yet; those arrive in P7a. The page in static/ is a stopgap that P7b's
React app replaces. Endpoints only read: nothing here writes to the database.
"""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from ..store import Database

# Display order for the pipeline panel (graph order, plan §3).
STAGE_ORDER = ["plan", "query", "approve_queries", "fetch", "dedup", "rerank", "extract", "coverage", "cluster",
               "normalize_terms", "conflict", "synthesize", "cite", "check", "critique", "route", "assemble"]


def create_app(db_path: str | Path) -> FastAPI:
    db = Database(db_path)
    app = FastAPI(title="BioLitSynth dashboard (read-only)")
    app.state.db = db
    app.router.on_shutdown.append(db.close)

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

    @app.get("/api/runs")
    def list_runs() -> list[dict]:
        rows = db.query("SELECT run_id, question, status, created_at FROM runs ORDER BY created_at DESC")
        return [{**dict(r), "counts": counts(r["run_id"])} for r in rows]

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
        return {
            "run_id": r["run_id"], "question": r["question"], "status": r["status"], "created_at": r["created_at"],
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
            "cache_hits": sum(e["cache_hit"] for e in events),
        }}

    return app
