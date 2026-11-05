"""GeminiClient guards and retries, with the SDK call replaced (no network)."""
import pytest
from google.genai import errors
from pydantic import ValidationError

from biolitsynth.config import ProviderConfig
from biolitsynth.llm import GeminiClient, MissingApiKey, ModelNotVerified
from biolitsynth.schemas import QueryDraft

CFG = ProviderConfig(api_key_env="GEMINI_API_KEY", rpm=10, max_retries=2)
GOOD = '{"query_string": "a AND b", "rationale": "r"}'


def scripted(*steps):
    """Each step is a JSON string to return or an exception to raise."""
    calls, steps = [], list(steps)

    async def generate(prompt, schema, system):
        calls.append((prompt, schema, system))
        s = steps.pop(0)
        if isinstance(s, BaseException):
            raise s
        return s, (10, 5)
    return generate, calls


def api_error(code):
    return errors.ClientError(code, {"error": {"code": code, "message": "x", "status": "X"}}) if code < 500 \
        else errors.ServerError(code, {"error": {"code": code, "message": "x", "status": "X"}})


async def nosleep(_):
    pass


def test_placeholder_model_ids_are_refused():
    with pytest.raises(ModelNotVerified, match="TODO-verify-gemini-flash"):
        GeminiClient("TODO-verify-gemini-flash", CFG, env={"GEMINI_API_KEY": "k"})


def test_missing_key_names_the_variable_not_a_value():
    with pytest.raises(MissingApiKey, match="GEMINI_API_KEY"):
        GeminiClient("gemini-x", CFG, env={})


async def test_returns_parsed_output_usage_and_passes_the_system_prompt():
    gen, calls = scripted(GOOD)
    out, usage = await GeminiClient("gemini-x", CFG, generate=gen).complete_with_usage("u", QueryDraft, system="s")
    assert out == QueryDraft(query_string="a AND b", rationale="r") and usage == (10, 5)
    assert calls == [("u", QueryDraft, "s")]


async def test_rate_limit_and_server_errors_back_off_then_succeed():
    slept = []

    async def sleep(s):
        slept.append(s)
    gen, calls = scripted(api_error(429), api_error(503), GOOD)
    out = await GeminiClient("gemini-x", CFG, generate=gen, sleep=sleep).complete("u", QueryDraft)
    assert out.query_string == "a AND b" and len(calls) == 3 and slept == [2.0, 4.0]


async def test_retries_stop_at_the_configured_cap():
    gen, calls = scripted(api_error(429), api_error(429), api_error(429), GOOD)
    with pytest.raises(errors.ClientError):
        await GeminiClient("gemini-x", CFG, generate=gen, sleep=nosleep).complete("u", QueryDraft)
    assert len(calls) == 3                                     # first try + max_retries=2


async def test_client_errors_other_than_429_are_not_retried():
    gen, calls = scripted(api_error(400), GOOD)
    with pytest.raises(errors.ClientError):
        await GeminiClient("gemini-x", CFG, generate=gen, sleep=nosleep).complete("u", QueryDraft)
    assert len(calls) == 1


async def test_invalid_output_gets_exactly_one_repair_retry():
    gen, calls = scripted("not json", GOOD)
    assert (await GeminiClient("gemini-x", CFG, generate=gen).complete("u", QueryDraft)).rationale == "r"
    gen, calls = scripted('{"query_string": "a"}', "still not json", GOOD)
    with pytest.raises(ValidationError):
        await GeminiClient("gemini-x", CFG, generate=gen).complete("u", QueryDraft)
    assert len(calls) == 2
