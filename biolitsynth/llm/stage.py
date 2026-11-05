"""StageLLM: the only way an agent calls a model (CLAUDE.md invariant 3).

Each call goes: trace span → LLM cache → run budget → concurrency cap + rate limiter → provider client.
A cache hit costs nothing and still emits a span (cache_hit=True), so the trace shows every model output used.

Cache key (plan §6.3): sha256(model, prompt_version, system prompt, rendered user message, output schema). Bumping a
prompt version or changing its text invalidates that stage only.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Protocol, Sequence

from ..pubmed.ratelimit import TokenBucket
from ..schemas import Stage
from ..store import Database
from ..trace import Tracer
from .base import LLMClient, T
from .prompts import RenderedPrompt

Usage = tuple[int | None, int | None]       # (tokens_in, tokens_out)


class BudgetExceeded(RuntimeError):
    pass


class CallBudget:
    """Shared per-run counter of real (uncached) LLM calls (plan §8.2)."""

    def __init__(self, max_calls: int | None):
        self.max_calls = max_calls
        self.used = 0

    def charge(self) -> None:
        if self.max_calls is not None and self.used >= self.max_calls:
            raise BudgetExceeded(f"run budget of {self.max_calls} LLM calls used up")
        self.used += 1


class _WithUsage(Protocol):
    async def complete_with_usage(self, prompt: str, schema: type[T], *, system: str | None = None) -> tuple[T, Usage | None]:
        ...


def cache_key(model: str, prompt: RenderedPrompt, schema: type) -> str:
    blob = json.dumps([model, prompt.version, prompt.system, prompt.user, schema.__name__,
                       schema.model_json_schema()], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


class StageLLM:
    def __init__(self, client: LLMClient, *, model: str, stage: Stage, agent: str, db: Database, tracer: Tracer,
                 limiter: TokenBucket, semaphore: asyncio.Semaphore, budget: CallBudget):
        self.client, self.model, self.stage, self.agent = client, model, stage, agent
        self.db, self.tracer, self.limiter, self.semaphore, self.budget = db, tracer, limiter, semaphore, budget
        self.calls = 0          # uncached calls made through this stage

    async def complete(self, prompt: RenderedPrompt, schema: type[T], *, input_refs: Sequence[str] = (),
                       parent: str | None = None) -> T:
        key = cache_key(self.model, prompt, schema)
        async with self.tracer.span(self.stage, self.agent, event_type="llm_call", input_refs=input_refs,
                                    parent=parent) as sp:
            sp.model, sp.prompt_version, sp.prompt_hash = self.model, prompt.version, prompt.hash
            cached = self.db.get_llm_cache(key)
            if cached is not None:
                sp.cache_hit = True
                sp.message = f"{schema.__name__} (cache hit)"
                return schema.model_validate_json(cached)
            self.budget.charge()
            async with self.semaphore:
                await self.limiter.acquire()
                self.calls += 1
                if hasattr(self.client, "complete_with_usage"):
                    out, usage = await self.client.complete_with_usage(prompt.user, schema, system=prompt.system)
                else:
                    out, usage = await self.client.complete(prompt.user, schema, system=prompt.system), None
            if usage:
                sp.tokens_in, sp.tokens_out = usage
            self.db.put_llm_cache(key, self.model, prompt.version, out.model_dump_json())
            sp.message = schema.__name__
            return out
