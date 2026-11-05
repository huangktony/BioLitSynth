"""SQLite access layer: plain sqlite3, one connection, WAL, one lock around every statement.

Graph fan-out runs many tasks at once; a single connection guarded by a lock keeps writes
serialised instead of racing ("database is locked"). Nothing here is async: statements are fast.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Sequence

from pydantic import BaseModel

from ..schemas import Paper, ResearchPlan, TraceEvent


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(resources.files("biolitsynth.store").joinpath("schema.sql").read_text())
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- generic -----------------------------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def insert(self, table: str, row: dict[str, Any], *, replace: bool = False) -> None:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        verb = "INSERT OR REPLACE" if replace else "INSERT"
        self.execute(f"{verb} INTO {table} ({cols}) VALUES ({marks})", list(row.values()))

    # --- runs --------------------------------------------------------------------------------
    def create_run(self, run_id: str, question: str, config: BaseModel | dict) -> None:
        cfg = config.model_dump(mode="json") if isinstance(config, BaseModel) else config
        self.insert("runs", {"run_id": run_id, "question": question, "status": "running",
                             "created_at": utcnow_iso(), "config_json": json.dumps(cfg)})

    def set_run_status(self, run_id: str, status: str) -> None:
        self.execute("UPDATE runs SET status = ? WHERE run_id = ?", (status, run_id))

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        rows = self.query("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        return rows[0] if rows else None

    def save_plan(self, plan: ResearchPlan) -> None:
        self.execute("UPDATE runs SET plan_json = ? WHERE run_id = ?", (plan.model_dump_json(), plan.run_id))
        self.executemany(
            "INSERT OR REPLACE INTO sub_questions (run_id, id, text, facet, rationale) VALUES (?,?,?,?,?)",
            [(plan.run_id, s.id, s.text, s.facet.value, s.rationale) for s in plan.sub_questions],
        )

    def get_plan(self, run_id: str) -> ResearchPlan | None:
        row = self.get_run(run_id)
        return ResearchPlan.model_validate_json(row["plan_json"]) if row and row["plan_json"] else None

    # --- papers (shared across runs) and per-run provenance -----------------------------------
    def upsert_paper(self, p: Paper) -> None:
        self.insert("papers", {
            "pmid": p.pmid, "title": p.title, "authors_json": json.dumps(p.authors), "abstract": p.abstract,
            "abstract_sha256": p.abstract_sha256, "journal": p.journal, "year": p.year, "doi": p.doi,
            "pub_types_json": json.dumps(p.publication_types), "mesh_json": json.dumps(p.mesh_terms),
            "registry_ids_json": json.dumps(p.registry_ids), "is_retracted": int(p.is_retracted),
            "fetched_at": utcnow_iso(),
        }, replace=True)

    def get_papers(self, pmids: Sequence[str]) -> dict[str, Paper]:
        """Per-run fields (retrieved_by, rank_by_query, study_id, relevance) are not stored on `papers`."""
        if not pmids:
            return {}
        rows = self.query(f"SELECT * FROM papers WHERE pmid IN ({','.join('?' * len(pmids))})", list(pmids))
        return {r["pmid"]: Paper(
            pmid=r["pmid"], title=r["title"], abstract=r["abstract"], abstract_sha256=r["abstract_sha256"],
            authors=json.loads(r["authors_json"] or "[]"), journal=r["journal"], year=r["year"], doi=r["doi"],
            publication_types=json.loads(r["pub_types_json"] or "[]"), mesh_terms=json.loads(r["mesh_json"] or "[]"),
            registry_ids=json.loads(r["registry_ids_json"] or "[]"), is_retracted=bool(r["is_retracted"]),
            retrieved_by=[], rank_by_query={},
        ) for r in rows}

    def add_run_papers(self, run_id: str, query_id: str, ranked: Iterable[tuple[str, int]]) -> None:
        self.executemany("INSERT OR REPLACE INTO run_papers (run_id, pmid, query_id, rank) VALUES (?,?,?,?)",
                         [(run_id, pmid, query_id, rank) for pmid, rank in ranked])

    def run_paper_entries(self, run_id: str) -> list[sqlite3.Row]:
        return self.query("SELECT pmid, query_id, rank FROM run_papers WHERE run_id = ? ORDER BY query_id, rank",
                          (run_id,))

    # --- esearch cache (plan §6.3) ---------------------------------------------------------------
    def get_esearch(self, key: str, *, max_age_s: float, now: float) -> tuple[int, list[str]] | None:
        rows = self.query("SELECT total, pmids_json, created_at FROM esearch_cache WHERE key = ?", (key,))
        if not rows or now - rows[0]["created_at"] > max_age_s:
            return None
        return rows[0]["total"], json.loads(rows[0]["pmids_json"])

    def put_esearch(self, key: str, query: str, retmax: int, sort: str, total: int, pmids: list[str], now: float) -> None:
        self.insert("esearch_cache", {"key": key, "query": query, "retmax": retmax, "sort": sort, "total": total,
                                      "pmids_json": json.dumps(pmids), "created_at": now}, replace=True)

    # --- LLM cache (plan §6.3) --------------------------------------------------------------------
    def get_llm_cache(self, key: str) -> str | None:
        rows = self.query("SELECT response_json FROM llm_cache WHERE key = ?", (key,))
        return rows[0]["response_json"] if rows else None

    def put_llm_cache(self, key: str, model: str, prompt_version: str | None, response_json: str) -> None:
        self.insert("llm_cache", {"key": key, "model": model, "prompt_version": prompt_version,
                                  "response_json": response_json, "created_at": utcnow_iso()}, replace=True)

    # --- trace -------------------------------------------------------------------------------
    def insert_trace_event(self, e: TraceEvent) -> None:
        self.insert("trace_events", {
            "run_id": e.run_id, "span_id": e.span_id, "parent_span_id": e.parent_span_id,
            "stage": e.stage, "agent": e.agent, "event_type": e.event_type, "model": e.model,
            "input_refs_json": json.dumps(e.input_refs), "output_refs_json": json.dumps(e.output_refs),
            "prompt_version": e.prompt_version, "prompt_hash": e.prompt_hash,
            "tokens_in": e.tokens_in, "tokens_out": e.tokens_out, "latency_ms": e.latency_ms,
            "cache_hit": int(e.cache_hit), "status": e.status, "message": e.message,
            "ts": e.ts.isoformat(),
        })

    def trace_events(self, run_id: str) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM trace_events WHERE run_id = ? ORDER BY ts, rowid", (run_id,))

    def export_trace_jsonl(self, run_id: str, path: str | Path) -> int:
        rows = self.trace_events(run_id)
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w") as f:
            for r in rows:
                d = dict(r)
                d["input_refs"] = json.loads(d.pop("input_refs_json") or "[]")
                d["output_refs"] = json.loads(d.pop("output_refs_json") or "[]")
                f.write(json.dumps(d) + "\n")
        return len(rows)
