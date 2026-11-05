"""OpenAI-compatible client against a mock server (no network)."""
import json

import httpx
import pytest
from pydantic import ValidationError

from biolitsynth.config import ProviderConfig
from biolitsynth.llm import MissingApiKey, ModelNotVerified, OpenAICompatClient
from biolitsynth.llm.retry import ProviderHTTPError
from biolitsynth.schemas import QueryDraft

CFG = ProviderConfig(api_key_env="NVIDIA_API_KEY", base_url="https://nim.example/v1", rpm=40, max_retries=2)
GOOD = {"query_string": "a AND b", "rationale": "r"}


class Server:
    def __init__(self, *steps):
        self.steps, self.requests = list(steps), []
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, req):
        self.requests.append(req)
        s = self.steps.pop(0)
        if isinstance(s, int):
            return httpx.Response(s, text="boom")
        content = s if isinstance(s, str) else json.dumps(s)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                         "usage": {"prompt_tokens": 11, "completion_tokens": 4}})


async def nosleep(_):
    pass


def client(server, cfg=CFG):
    return OpenAICompatClient("vendor/model-x", cfg, env={"NVIDIA_API_KEY": "k"}, transport=server.transport,
                              sleep=nosleep)


def test_guards():
    with pytest.raises(ModelNotVerified):
        OpenAICompatClient("TODO-verify-nim-critic", CFG, env={"NVIDIA_API_KEY": "k"})
    with pytest.raises(MissingApiKey, match="NVIDIA_API_KEY"):
        OpenAICompatClient("m", CFG, env={})


async def test_request_shape_auth_and_parsed_output():
    s = Server(GOOD)
    out, usage = await client(s).complete_with_usage("user msg", QueryDraft, system="sys")
    assert out == QueryDraft(**GOOD) and usage == (11, 4)
    req = s.requests[0]
    assert str(req.url) == "https://nim.example/v1/chat/completions" and req.headers["authorization"] == "Bearer k"
    body = json.loads(req.content)
    assert body["model"] == "vendor/model-x" and body["temperature"] == 0
    assert body["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "user msg"}]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"]["properties"].keys() == {"query_string", "rationale"}


async def test_json_object_mode_puts_the_schema_in_the_system_prompt():
    s = Server(GOOD)
    await client(s, CFG.model_copy(update={"structured": "json_object"})).complete("u", QueryDraft, system="sys")
    body = json.loads(s.requests[0].content)
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["content"].startswith("sys") and '"query_string"' in body["messages"][0]["content"]


async def test_retries_429_and_5xx_but_not_400():
    s = Server(429, 503, GOOD)
    assert (await client(s).complete("u", QueryDraft)).rationale == "r" and len(s.requests) == 3
    s = Server(400, GOOD)
    with pytest.raises(ProviderHTTPError, match="HTTP 400"):
        await client(s).complete("u", QueryDraft)
    assert len(s.requests) == 1


async def test_one_repair_retry_on_invalid_output():
    s = Server("nope", GOOD)
    assert (await client(s).complete("u", QueryDraft)).query_string == "a AND b"
    s = Server("nope", "still nope", GOOD)
    with pytest.raises(ValidationError):
        await client(s).complete("u", QueryDraft)
