"""OpenAI-compatible chat-completions client (plan §7): NVIDIA NIM, Groq, OpenRouter.

Plain httpx, no SDK. Structured output either as `response_format: json_schema` (default) or, for servers without
it, `json_object` with the JSON schema appended to the system prompt. Retries follow llm/retry.py.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Awaitable, Callable, Mapping

import httpx

from ..config import ProviderConfig
from .base import T
from .gemini import PLACEHOLDER, MissingApiKey, ModelNotVerified
from .retry import ProviderHTTPError, Usage, complete_with_retries


class OpenAICompatClient:
    def __init__(self, model: str, cfg: ProviderConfig, *, env: Mapping[str, str] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        if not model or model.startswith(PLACEHOLDER):
            raise ModelNotVerified(f"model id {model!r} is a placeholder: check the provider's model list by hand "
                                   f"and set it in config.yaml (models.*)")
        if not cfg.base_url:
            raise ValueError("an OpenAI-compatible provider needs base_url in config.yaml")
        env = os.environ if env is None else env
        key = env.get(cfg.api_key_env)
        if not key:
            raise MissingApiKey(f"set the {cfg.api_key_env} environment variable (in .env, never in the repo)")
        self.model, self.cfg, self._sleep = model, cfg, sleep
        self._http = httpx.AsyncClient(base_url=cfg.base_url.rstrip("/") + "/", transport=transport,
                                       timeout=cfg.timeout_s, headers={"Authorization": f"Bearer {key}"})

    async def aclose(self) -> None:
        await self._http.aclose()

    def _body(self, prompt: str, schema: type, system: str | None) -> dict:
        js = schema.model_json_schema()
        if self.cfg.structured == "json_schema":
            fmt = {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": js}}
        else:
            system = (system or "") + "\n\nReturn only a JSON object that matches this JSON schema:\n" + json.dumps(js)
            fmt = {"type": "json_object"}
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        return {"model": self.model, "messages": messages, "temperature": 0, "response_format": fmt}

    async def _generate(self, prompt: str, schema: type, system: str | None) -> tuple[str, Usage | None]:
        try:
            resp = await self._http.post("chat/completions", json=self._body(prompt, schema, system))
        except httpx.TransportError as e:
            raise ProviderHTTPError(503, f"transport error: {e}") from e
        if resp.status_code >= 400:
            raise ProviderHTTPError(resp.status_code, resp.text)
        data = resp.json()
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        u = data.get("usage") or {}
        return text, ((u.get("prompt_tokens"), u.get("completion_tokens")) if u else None)

    async def complete_with_usage(self, prompt: str, schema: type[T], *, system: str | None = None) -> tuple[T, Usage | None]:
        return await complete_with_retries(self._generate, prompt, schema, system,
                                           max_retries=self.cfg.max_retries, sleep=self._sleep)

    async def complete(self, prompt: str, schema: type[T], *, system: str | None = None) -> T:
        return (await self.complete_with_usage(prompt, schema, system=system))[0]
