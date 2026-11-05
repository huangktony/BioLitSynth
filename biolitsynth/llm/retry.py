"""Shared retry policy for provider clients (plan §7, §8.3).

429 and 5xx back off exponentially (2 s, 4 s, … capped at 60 s) up to `max_retries`. Output that fails schema
validation gets exactly one repair retry, then the ValidationError is raised.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from pydantic import ValidationError

from .base import T

Usage = tuple[int | None, int | None]                     # (tokens_in, tokens_out)
Generate = Callable[[str, type, "str | None"], Awaitable[tuple[str, "Usage | None"]]]
RETRYABLE = {429, 500, 502, 503, 504}


class ProviderHTTPError(RuntimeError):
    def __init__(self, code: int, detail: str = ""):
        super().__init__(f"HTTP {code}: {detail[:300]}")
        self.code = code


def retryable(exc: BaseException) -> bool:
    return getattr(exc, "code", None) in RETRYABLE


async def complete_with_retries(generate: Generate, prompt: str, schema: type[T], system: str | None, *,
                                max_retries: int, sleep: Callable[[float], Awaitable[None]]) -> tuple[T, Usage | None]:
    attempt, repaired = 0, False
    while True:
        try:
            text, usage = await generate(prompt, schema, system)
        except Exception as exc:
            if retryable(exc) and attempt < max_retries:
                await sleep(min(2.0 * 2 ** attempt, 60.0))
                attempt += 1
                continue
            raise
        try:
            return schema.model_validate_json(text), usage
        except ValidationError:
            if repaired:
                raise
            repaired = True
