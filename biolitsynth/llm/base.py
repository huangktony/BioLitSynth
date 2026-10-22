"""Thin LLM client protocol (plan §7). Real clients (Gemini, OpenAI-compatible, Ollama) arrive in P2."""
from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    async def complete(self, prompt: str, schema: type[T], *, system: str | None = None) -> T:
        """Return `schema` parsed from the model's structured output. Invalid output raises."""
        ...
