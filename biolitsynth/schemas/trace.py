"""Plan §4.7: handoff & trace models."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TaskSpec(BaseModel):
    """Typed handoff from orchestrator to any subagent (Anthropic: objective, format, tools, boundaries)."""
    task_id: str
    agent: str
    objective: str
    input_refs: list[str]              # artifact ids, e.g. ["sq2"] or ["pmid:123", "pmid:456"]
    output_schema: str                 # e.g. "EvidenceItem[]"
    tools: list[str] = []
    constraints: list[str]             # e.g. "quote must be verbatim", "do not use outside knowledge"
    budget: dict[str, int]             # {"max_calls": 3, "max_output_tokens": 4000}


Stage = Literal["plan", "approve_queries", "query", "fetch", "dedup", "extract", "coverage",
                "rerank", "cluster", "normalize_terms", "conflict", "synthesize", "cite", "check",
                "critique", "route", "assemble"]
EventType = Literal["llm_call", "tool_call", "handoff", "validation", "retry", "decision", "error"]


class TraceEvent(BaseModel):
    run_id: str
    span_id: str
    parent_span_id: str | None
    stage: Stage
    agent: str
    event_type: EventType
    model: str | None = None
    input_refs: list[str] = []
    output_refs: list[str] = []
    prompt_version: str | None = None
    prompt_hash: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    cache_hit: bool = False
    status: Literal["ok", "retry", "fail"]
    message: str | None = None         # human-readable: "q2 refined: 0 → 143 hits"
    ts: datetime
