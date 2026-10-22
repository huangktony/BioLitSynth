import pytest
from pydantic import ValidationError

from biolitsynth.llm import FakeClient, FakeClientExhausted
from biolitsynth.schemas import CoverageDecision


async def test_replays_in_order_and_records_calls():
    c = FakeClient({"CoverageDecision": [{"sufficient": True}, CoverageDecision(sufficient=False)]})
    assert (await c.complete("p1", CoverageDecision, system="s")).sufficient is True
    assert (await c.complete("p2", CoverageDecision)).sufficient is False
    assert [x["prompt"] for x in c.calls] == ["p1", "p2"] and c.calls[0]["system"] == "s"


async def test_exhausted_queue_fails_loudly():
    with pytest.raises(FakeClientExhausted):
        await FakeClient().complete("p", CoverageDecision)


async def test_invalid_fixture_is_rejected_by_the_real_schema():
    with pytest.raises(ValidationError):
        await FakeClient({"CoverageDecision": [{"sufficient": "banana"}]}).complete("p", CoverageDecision)
