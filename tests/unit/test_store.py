import json
import sqlite3

import pytest

from biolitsynth.schemas import Facet, ResearchPlan, SubQuestion
from biolitsynth.store import Database, read_snapshot, snapshot_path, write_snapshot


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def test_schema_applied_and_wal(db):
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "sub_questions", "queries", "papers", "run_papers", "study_clusters", "evidence",
            "evidence_subq", "relevance_scores", "term_map", "conflicts", "omitted_evidence", "claims",
            "claim_evidence", "checks", "verdicts", "trace_events", "llm_cache"} <= tables
    assert db.query("PRAGMA journal_mode")[0][0] == "wal"


def test_reopening_is_idempotent(tmp_path):
    Database(tmp_path / "t.db").close()
    Database(tmp_path / "t.db").close()


def ev_row(run_id):
    return {"run_id": run_id, "id": "ev_1_1", "pmid": "1", "statement": "s", "quote": "q", "subject": "a",
            "outcome": "b", "direction": "increases", "organism": "human", "study_design": "rct"}


def test_same_evidence_id_allowed_in_two_runs_but_not_twice_in_one(db):
    # ids like ev_<pmid>_<n> repeat across runs, so the key is (run_id, id), not id alone.
    db.insert("evidence", ev_row("run_a"))
    db.insert("evidence", ev_row("run_b"))
    with pytest.raises(sqlite3.IntegrityError):
        db.insert("evidence", ev_row("run_a"))


def test_claims_keyed_by_attempt(db):
    base = {"run_id": "r", "id": "cl1", "text": "t", "claim_type": "gap", "confidence": "low"}
    db.insert("claims", {**base, "attempt": 0})
    db.insert("claims", {**base, "attempt": 1})
    assert len(db.query("SELECT * FROM claims")) == 2


def test_plan_saved_to_runs_and_sub_questions(db):
    db.create_run("r", "q?", {"a": 1})
    plan = ResearchPlan(run_id="r", question="q?", effort="simple", sub_questions=[
        SubQuestion(id=f"sq{i}", text="t", facet=Facet.OTHER, rationale="r", key_concepts=[]) for i in (1, 2)])
    db.save_plan(plan)
    assert ResearchPlan.model_validate_json(db.get_run("r")["plan_json"]) == plan
    assert len(db.query("SELECT * FROM sub_questions WHERE run_id='r'")) == 2


def test_snapshot_roundtrip_and_naming(tmp_path):
    p = write_snapshot(tmp_path, 6, "claims", [{"b": 1, "a": 2}], attempt=0)
    assert p.name == "06_claims_a0.json" and p == snapshot_path(tmp_path, 6, "claims", 0)
    assert read_snapshot(tmp_path, 6, "claims", 0) == [{"b": 1, "a": 2}]
    assert write_snapshot(tmp_path, 1, "plan", {}).name == "01_plan.json"
