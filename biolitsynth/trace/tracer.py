"""Tracer (plan §6.4): spans written to SQLite as TraceEvents.

Parenting: an explicit `parent=` always wins; otherwise the enclosing span from a contextvar is used.
Graph nodes pass the parent explicitly (it travels in RunState / Send payloads) because context is not
guaranteed to cross node boundaries. Inside a node, nesting and asyncio.gather fan-out use the contextvar.
"""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import AsyncIterator, Sequence

from ..schemas import EventType, Stage, TraceEvent
from ..store import Database

_current_span: ContextVar[str | None] = ContextVar("biolitsynth_current_span", default=None)


class SpanHandle:
    """Mutable view of a span still open. Set outputs before the `async with` block exits."""

    def __init__(self, span_id: str, parent_span_id: str | None):
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.output_refs: list[str] = []
        self.message: str | None = None
        self.status: str = "ok"
        self.model: str | None = None
        self.prompt_version: str | None = None
        self.prompt_hash: str | None = None
        self.tokens_in: int | None = None
        self.tokens_out: int | None = None
        self.cache_hit: bool = False


class Tracer:
    def __init__(self, db: Database, run_id: str):
        self.db = db
        self.run_id = run_id

    @asynccontextmanager
    async def span(self, stage: Stage, agent: str, *, event_type: EventType = "tool_call",
                   input_refs: Sequence[str] = (), parent: str | None = None) -> AsyncIterator[SpanHandle]:
        handle = SpanHandle(uuid.uuid4().hex, parent if parent is not None else _current_span.get())
        token = _current_span.set(handle.span_id)
        start = time.perf_counter()
        try:
            yield handle
        except BaseException as exc:
            handle.status = "fail"
            handle.message = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            _current_span.reset(token)
            self.db.insert_trace_event(TraceEvent(
                run_id=self.run_id, span_id=handle.span_id, parent_span_id=handle.parent_span_id,
                stage=stage, agent=agent, event_type=event_type, model=handle.model,
                input_refs=list(input_refs), output_refs=handle.output_refs,
                prompt_version=handle.prompt_version, prompt_hash=handle.prompt_hash,
                tokens_in=handle.tokens_in, tokens_out=handle.tokens_out,
                latency_ms=int((time.perf_counter() - start) * 1000), cache_hit=handle.cache_hit,
                status=handle.status, message=handle.message, ts=datetime.now(timezone.utc),
            ))

    async def event(self, stage: Stage, agent: str, event_type: EventType, *, message: str | None = None,
                    input_refs: Sequence[str] = (), output_refs: Sequence[str] = (),
                    parent: str | None = None, status: str = "ok") -> str:
        """One instantaneous event (handoff, decision, retry). Returns its span_id."""
        async with self.span(stage, agent, event_type=event_type, input_refs=input_refs, parent=parent) as s:
            s.output_refs = list(output_refs)
            s.message = message
            s.status = status
        return s.span_id
