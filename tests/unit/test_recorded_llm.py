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
