"""Read-only dashboard API. Data comes from a real dry run plus one recorded PubMed fixture; each response is
checked against the store directly, not against answers written alongside the API code."""
import pytest
from fastapi.testclient import TestClient

from biolitsynth.api import create_app
from biolitsynth.graph import run_dry
from biolitsynth.pubmed import parse_pubmed_xml
from biolitsynth.store import Database

from ..pubmed_mock import fixture

Q = "Does metformin reduce cancer risk?"


@pytest.fixture
async def client(settings):
    s = await run_dry(Q, settings)
    db_path = settings.paths.data_dir / "biolitsynth.db"
    paper = parse_pubmed_xml(fixture("structured_rct_42897521")).papers[0]
    db = Database(db_path)
    db.upsert_paper(paper)
    db.add_run_papers(s.run_id, "q1", [(paper.pmid, 1)])
    db.close()
    with TestClient(create_app(db_path)) as c:
        yield c, s, paper, Database(db_path)


def test_runs_list_and_detail_match_the_store(client):
    c, s, _, db = client
    runs = c.get("/api/runs").json()
    assert [r["run_id"] for r in runs] == [s.run_id]
    assert runs[0]["status"] == "completed_dry"

    d = c.get(f"/api/runs/{s.run_id}").json()
    assert d["question"] == Q
    assert [q["id"] for q in d["queries"]] == ["q1", "q2", "q3", "q4"]        # numeric order, gap-round q4 last
    assert d["counts"]["gap_rounds"] == s.state["coverage_rounds"]
    assert d["counts"]["synth_attempts"] == s.state["synth_attempt"] + 1
    assert d["counts"]["trace_events"] == len(db.trace_events(s.run_id))
    assert {st["stage"] for st in d["stages"]} == {e["stage"] for e in db.trace_events(s.run_id)}
    assert sum(st["events"] for st in d["stages"]) == d["counts"]["trace_events"]
    assert len(d["coverage"]) == len([e for e in db.trace_events(s.run_id) if e["stage"] == "coverage"])


def test_claims_show_the_latest_attempt_once(client):
    c, s, _, db = client
    claims = c.get(f"/api/runs/{s.run_id}/claims").json()
    assert sorted(cl["id"] for cl in claims) == sorted(s.state["claim_ids"])
    for cl in claims:
        rows = db.query("SELECT attempt FROM claims WHERE run_id = ? AND id = ?", (s.run_id, cl["id"]))
        assert cl["attempt"] == max(r["attempt"] for r in rows)
        assert cl["attempts"] == len(rows)


def test_papers_and_trace(client):
    c, s, paper, db = client
    papers = c.get(f"/api/runs/{s.run_id}/papers").json()
    assert papers == [{"pmid": paper.pmid, "title": paper.title, "journal": paper.journal, "year": paper.year,
                       "is_retracted": False, "rank_by_query": {"q1": 1}}]
    t = c.get(f"/api/runs/{s.run_id}/trace").json()
    assert t["totals"]["events"] == len(t["events"]) == len(db.trace_events(s.run_id))


def test_unknown_run_is_404_and_page_is_served(client):
    c, *_ = client
    assert c.get("/api/runs/run_nope").status_code == 404
    assert c.get("/api/runs/run_nope/claims").status_code == 404
    page = c.get("/")
    assert page.status_code == 200 and "/api/runs" in page.text


def test_api_never_writes(client):
    c, s, _, db = client
    tables = [r[0] for r in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")]
    before = {t: db.query(f"SELECT COUNT(*) FROM {t}")[0][0] for t in tables}
    for path in ("/api/runs", f"/api/runs/{s.run_id}", f"/api/runs/{s.run_id}/papers",
                 f"/api/runs/{s.run_id}/claims", f"/api/runs/{s.run_id}/trace"):
        assert c.get(path).status_code == 200
    assert {t: db.query(f"SELECT COUNT(*) FROM {t}")[0][0] for t in tables} == before
