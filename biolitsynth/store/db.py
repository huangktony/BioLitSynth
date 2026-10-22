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

from ..schemas import ResearchPlan, TraceEvent


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
