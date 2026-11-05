"""Gemini client (plan §7) on the google-genai SDK, with JSON-schema structured output.

Retries follow llm/retry.py (429/5xx backoff, one repair retry on invalid output). The SDK call is injectable (`generate=`) so tests never touch the network.
"""
from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable, Mapping

from ..config import ProviderConfig
from .base import T
from .retry import Generate, Usage, complete_with_retries

PLACEHOLDER = "TODO-verify"


class ModelNotVerified(ValueError):
    """config.yaml still holds a TODO-verify placeholder (CLAUDE.md invariant 5: never invent a model ID)."""


class MissingApiKey(RuntimeError):
    pass


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
        return await complete_with_retries(self._generate, prompt, schema, system,
                                           max_retries=self.cfg.max_retries, sleep=self._sleep)

    async def complete(self, prompt: str, schema: type[T], *, system: str | None = None) -> T:
        return (await self.complete_with_usage(prompt, schema, system=system))[0]
