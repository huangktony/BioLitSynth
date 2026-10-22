"""FakeClient: replays canned responses so tests never touch the network (plan §11.1).

Fixture rule: the expected answers must come from a person or from a recorded live run, never from the
code under test. A fixture that the same agent wrote alongside the logic proves nothing.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from pydantic import BaseModel

from .base import T


class FakeClientExhausted(AssertionError):
    pass


class FakeClient:
    def __init__(self, responses: dict[str, list[BaseModel | dict[str, Any]]] | None = None):
        """`responses` maps a schema class name (e.g. "ResearchPlan") to its queue of replies, in call order."""
        self._queues: dict[str, deque] = defaultdict(deque)
        for name, items in (responses or {}).items():
            self._queues[name].extend(items)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, prompt: str, schema: type[T], *, system: str | None = None) -> T:
        self.calls.append({"schema": schema.__name__, "prompt": prompt, "system": system})
        queue = self._queues[schema.__name__]
        if not queue:
            raise FakeClientExhausted(f"no canned response left for {schema.__name__}")
        item = queue.popleft()
        # Re-validate even model instances so fixtures are held to the real schema.
        data = item.model_dump() if isinstance(item, BaseModel) else item
        return schema.model_validate(data)
