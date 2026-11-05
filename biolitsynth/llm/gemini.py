"""Gemini client (plan §7) on the google-genai SDK, with JSON-schema structured output.

Retries: 429 and 5xx back off exponentially up to `max_retries`; output that fails schema validation gets one
repair retry (plan §8.3), then raises. The SDK call is injectable (`generate=`) so tests never touch the network.
"""
from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable, Mapping

from pydantic import ValidationError

from ..config import ProviderConfig
from .base import T

Usage = tuple[int | None, int | None]
Generate = Callable[[str, type, "str | None"], Awaitable[tuple[str, "Usage | None"]]]
RETRYABLE = {429, 500, 502, 503, 504}
PLACEHOLDER = "TODO-verify"


class ModelNotVerified(ValueError):
    """config.yaml still holds a TODO-verify placeholder (CLAUDE.md invariant 5: never invent a model ID)."""


class MissingApiKey(RuntimeError):
    pass


def _retryable(exc: BaseException) -> bool:
    return getattr(exc, "code", None) in RETRYABLE


class GeminiClient:
    def __init__(self, model: str, cfg: ProviderConfig, *, env: Mapping[str, str] | None = None,
                 generate: Generate | None = None, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        if not model or model.startswith(PLACEHOLDER):
            raise ModelNotVerified(f"model id {model!r} is a placeholder: check AI Studio by hand and set it in "
                                   f"config.yaml (models.*)")
        self.model, self.cfg, self._sleep = model, cfg, sleep
        if generate is None:
            env = os.environ if env is None else env
            key = env.get(cfg.api_key_env)
            if not key:
                raise MissingApiKey(f"set the {cfg.api_key_env} environment variable (never put the key in a file "
                                    f"in the repo)")
            generate = self._sdk(key)
        self._generate = generate

    def _sdk(self, api_key: str) -> Generate:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        async def generate(prompt: str, schema: type, system: str | None) -> tuple[str, Usage | None]:
            resp = await client.aio.models.generate_content(
                model=self.model, contents=prompt,
                config=types.GenerateContentConfig(system_instruction=system, temperature=0,
                                                   response_mime_type="application/json", response_schema=schema))
            u = resp.usage_metadata
            return resp.text or "", ((u.prompt_token_count, u.candidates_token_count) if u else None)

        return generate

    async def complete_with_usage(self, prompt: str, schema: type[T], *, system: str | None = None) -> tuple[T, Usage | None]:
        attempt, repaired = 0, False
        while True:
            try:
                text, usage = await self._generate(prompt, schema, system)
            except Exception as exc:
                if _retryable(exc) and attempt < self.cfg.max_retries:
                    await self._sleep(min(2.0 * 2 ** attempt, 60.0))
                    attempt += 1
                    continue
                raise
            try:
                return schema.model_validate_json(text), usage
            except ValidationError:
                if repaired:
                    raise
                repaired = True

    async def complete(self, prompt: str, schema: type[T], *, system: str | None = None) -> T:
        return (await self.complete_with_usage(prompt, schema, system=system))[0]
