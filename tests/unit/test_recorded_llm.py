"""Recorded live Query Builder replies (tests/fixtures/llm) still satisfy the current schema and draft checks."""
import json
from pathlib import Path

import pytest

from biolitsynth.agents.query_builder import check_draft
from biolitsynth.schemas import QueryDraft

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "llm" / "query_builder_live_20261105.json"
ROWS = json.loads(FIX.read_text())


def test_fixture_is_a_real_recording():
    assert len(ROWS) == 6 and {r["model"] for r in ROWS} == {"gemini-3.8-flash"}
    assert {r["prompt_version"] for r in ROWS} == {"query_builder.v1"}


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["created_at"])
def test_recorded_reply_parses_and_passes_the_draft_checks(row):
    q = check_draft(QueryDraft.model_validate_json(row["response_json"]))
    assert "[" in q and "metformin" in q.lower()            # uses field tags; keeps the shared key concept


def test_recorded_lead_reply_builds_a_plan_with_distinct_facets():
    """P2 acceptance, checked on the recorded live reply: golden Q1 → 3 sub-questions with distinct facets."""
    from biolitsynth.agents.lead import build_plan
    from biolitsynth.schemas import PlanDraft
    rows = json.loads((FIX.parent / "lead_live_20261105.json").read_text())
    assert len(rows) == 1 and rows[0]["prompt_version"] == "lead.v1"
    built = build_plan("r", "golden Q1", PlanDraft.model_validate_json(rows[0]["response_json"]), max_sub_questions=3)
    facets = [s.facet for s in built.plan.sub_questions]
    assert built.plan.effort == "moderate" and len(set(facets)) == 3 and built.warnings == []
