"""ScriptedClient: an LLM stand-in that answers by matching text in the prompt, for parallel graph branches.

FakeClient replays one queue per schema, so with several Query Builders running at once the replies could go to
the wrong branch. ScriptedClient picks the reply by the first `match` string found in the user message instead.
The replies are inputs (what a model might say), not expected answers; tests assert what the code does with them.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ScriptedClient:
    def __init__(self, script: list[tuple[str, dict[str, Any]]]):
        """`script`: (match, reply) pairs. A reply is used at most once, in order, among those that match."""
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, prompt: str, schema: type[BaseModel], *, system: str | None = None):
        self.calls.append({"schema": schema.__name__, "prompt": prompt, "system": system})
        for i, (match, reply) in enumerate(self.script):
            if match in prompt:
                del self.script[i]
                return schema.model_validate(reply)
        raise AssertionError(f"no scripted reply matches this prompt:\n{prompt}")
