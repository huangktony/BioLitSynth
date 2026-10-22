import asyncio

import pytest

from biolitsynth.store import Database
from biolitsynth.trace import Tracer


@pytest.fixture
def tr(tmp_path):
    db = Database(tmp_path / "t.db")
    yield Tracer(db, "r1")
    db.close()


def by_message(tr):
    return {e["message"]: e for e in tr.db.trace_events("r1")}


async def test_nested_spans_get_parents(tr):
    async with tr.span("plan", "lead") as outer:
        async with tr.span("query", "qb") as inner:
            inner.message = "inner"
        outer.message = "outer"
    ev = by_message(tr)
    assert ev["outer"]["parent_span_id"] is None
    assert ev["inner"]["parent_span_id"] == outer.span_id


async def test_parallel_children_all_parent_to_the_enclosing_span(tr):
    async def child(i):
        async with tr.span("fetch", "f") as s:
            await asyncio.sleep(0.01 * (3 - i))      # finish out of order
            s.message = f"c{i}"

    async with tr.span("plan", "lead") as outer:
        await asyncio.gather(*(child(i) for i in range(3)))
    kids = [e for e in tr.db.trace_events("r1") if e["message"] and e["message"].startswith("c")]
    assert len(kids) == 3
    assert {k["parent_span_id"] for k in kids} == {outer.span_id}


async def test_siblings_do_not_leak_context(tr):
    async with tr.span("plan", "lead"):
        pass
    async with tr.span("query", "qb") as later:
        pass
    assert later.parent_span_id is None


async def test_explicit_parent_wins_over_context(tr):
    async with tr.span("plan", "lead"):
        async with tr.span("fetch", "f", parent="given") as s:
            pass
    assert s.parent_span_id == "given"


async def test_failure_is_recorded_and_reraised(tr):
    with pytest.raises(ValueError):
        async with tr.span("extract", "x"):
            raise ValueError("boom")
    (e,) = tr.db.trace_events("r1")
    assert e["status"] == "fail" and "ValueError: boom" in e["message"]


async def test_event_returns_span_id_and_stores_refs(tr):
    sid = await tr.event("route", "router", "decision", input_refs=["cl1"], output_refs=["cl2"], message="m")
    (e,) = tr.db.trace_events("r1")
    assert e["span_id"] == sid and e["event_type"] == "decision"
    assert e["input_refs_json"] == '["cl1"]' and e["output_refs_json"] == '["cl2"]'
