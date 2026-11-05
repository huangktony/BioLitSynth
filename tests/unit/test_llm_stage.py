"""StageLLM: cache, budget, rate limit and one trace span per call (CLAUDE.md invariant 3)."""
import asyncio

import pytest

from biolitsynth.llm import BudgetExceeded, CallBudget, FakeClient, RenderedPrompt, StageLLM
from biolitsynth.pubmed import TokenBucket
from biolitsynth.schemas import QueryDraft
from biolitsynth.store import Database
from biolitsynth.trace import Tracer

P = RenderedPrompt(system="sys", user="write a query", version="query_builder.v1", hash="h1")
REPLY = {"query_string": "a AND b", "rationale": "r"}


class WithUsage(FakeClient):
    async def complete_with_usage(self, prompt, schema, *, system=None):
        return await self.complete(prompt, schema, system=system), (120, 30)


@pytest.fixture
def make(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_run("r1", "q", {})

    def _make(client, budget=None):
        return db, StageLLM(client, model="m-1", stage="query", agent="query_builder", db=db,
                            tracer=Tracer(db, "r1"), limiter=TokenBucket(10_000), semaphore=asyncio.Semaphore(2),
                            budget=budget or CallBudget(None))
    yield _make
    db.close()


async def test_second_identical_call_is_served_from_cache_and_still_traced(make):
    client = WithUsage({"QueryDraft": [REPLY]})
    db, llm = make(client)
    a = await llm.complete(P, QueryDraft, input_refs=["sq1"])
    b = await llm.complete(P, QueryDraft, input_refs=["sq1"])
    assert a == b == QueryDraft(**REPLY) and len(client.calls) == 1 and llm.calls == 1
    spans = [dict(r) for r in db.trace_events("r1")]
    assert [(s["event_type"], s["cache_hit"]) for s in spans] == [("llm_call", 0), ("llm_call", 1)]
    assert spans[0]["model"] == "m-1" and spans[0]["prompt_version"] == "query_builder.v1"
    assert (spans[0]["tokens_in"], spans[0]["tokens_out"]) == (120, 30)
    assert client.calls[0]["system"] == "sys"


@pytest.mark.parametrize("change", [{"version": "query_builder.v2"}, {"user": "other"}, {"system": "other"}])
async def test_prompt_version_or_text_change_misses_the_cache(make, change):
    client = FakeClient({"QueryDraft": [REPLY, REPLY]})
    _, llm = make(client)
    await llm.complete(P, QueryDraft)
    await llm.complete(RenderedPrompt(**{**P.__dict__, **change}), QueryDraft)
    assert len(client.calls) == 2


async def test_budget_counts_only_uncached_calls(make):
    client = FakeClient({"QueryDraft": [REPLY, REPLY]})
    db, llm = make(client, budget=CallBudget(1))
    await llm.complete(P, QueryDraft)
    await llm.complete(P, QueryDraft)                          # cache hit: free
    with pytest.raises(BudgetExceeded):
        await llm.complete(RenderedPrompt(**{**P.__dict__, "user": "new"}), QueryDraft)
    assert len(client.calls) == 1
    assert db.trace_events("r1")[-1]["status"] == "fail"


async def test_a_failed_call_is_not_cached(make):
    client = FakeClient({"QueryDraft": [{"rationale": "missing the query"}, REPLY]})
    _, llm = make(client)
    with pytest.raises(Exception):
        await llm.complete(P, QueryDraft)
    assert await llm.complete(P, QueryDraft) == QueryDraft(**REPLY)
