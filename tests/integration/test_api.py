"""Read-only dashboard API. Data comes from a real dry run plus one recorded PubMed fixture; each response is
checked against the store directly, not against answers written alongside the API code."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from biolitsynth.api import create_app
from biolitsynth.graph import run_dry
from biolitsynth.pubmed import TokenBucket, parse_pubmed_xml
from biolitsynth.store import Database

from ..llm_mock import ScriptedClient
from ..pubmed_mock import CRC_IDS, FakeNcbi, fixture

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
    with TestClient(create_app(settings)) as c:
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


# --- POST /api/fetch: the P1 fetch demo from the browser, against the fake NCBI --------------------
@pytest.fixture
def fetch_client(settings):
    ncbi = FakeNcbi()
    with TestClient(create_app(settings, transport=ncbi.transport)) as c:
        yield c, ncbi, Database(settings.paths.data_dir / "biolitsynth.db")


def test_fetch_creates_a_run_whose_papers_the_dashboard_lists(fetch_client):
    c, ncbi, db = fetch_client
    r = c.post("/api/fetch", json={"query": "metformin AND colorectal neoplasms[mh]", "retmax": 25})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["candidates"] == len(CRC_IDS[:25])
    assert db.get_run(body["run_id"])["status"] == "completed_fetch_demo"
    stored = {row["pmid"] for row in db.run_paper_entries(body["run_id"])}
    assert len(stored) == body["stored"] > 0
    assert set(ncbi.fetched_ids()) >= stored                     # every stored paper came from efetch
    listed = c.get(f"/api/runs/{body['run_id']}/papers").json()
    assert {p["pmid"] for p in listed} == stored
    assert [r["run_id"] for r in c.get("/api/runs").json()][0] == body["run_id"]   # newest first

    again = c.post("/api/fetch", json={"query": "metformin AND colorectal neoplasms[mh]", "retmax": 25}).json()
    assert again["network_calls"] == 0 and again["search_cached"]


@pytest.mark.parametrize("payload", [{"query": ""}, {"query": "ab"}, {"query": "metformin", "retmax": 0},
                                     {"query": "metformin", "retmax": 201}, {}])
def test_fetch_rejects_bad_input_without_calling_ncbi(fetch_client, payload):
    c, ncbi, db = fetch_client
    assert c.post("/api/fetch", json=payload).status_code == 422
    assert ncbi.requests == [] and db.query("SELECT COUNT(*) FROM runs")[0][0] == 0


def test_failed_fetch_is_502_and_the_run_is_marked_failed(fetch_client):
    c, ncbi, db = fetch_client
    ncbi.esearch_override = {"not": "an esearch response"}
    r = c.post("/api/fetch", json={"query": "metformin"})
    assert r.status_code == 502 and "PubMed fetch failed" in r.json()["detail"]
    assert [row["status"] for row in db.query("SELECT status FROM runs")] == ["failed"]


# --- POST /api/runs: question -> Lead plan -> Query Builder -> fetch -> dedup, all offline ---------
LEAD_FIXTURE = Path(__file__).parents[1] / "fixtures" / "llm" / "lead_live_20261105.json"
FAST = {"limiters": {"gemini": TokenBucket(10_000)}, "ncbi_limiter": TokenBucket(10_000), "ncbi_env": {}}
RUN_Q = "Does metformin reduce cancer incidence in type 2 diabetes, and through what mechanisms?"


def run_client(settings, clients):
    ncbi = FakeNcbi()   # default: every query reports len(CRC_IDS) hits, inside the 20-2000 band
    overrides = {"clients": clients, **FAST} if clients is not None else {}
    return TestClient(create_app(settings, transport=ncbi.transport, live_overrides=overrides))


def test_post_run_plans_builds_queries_and_lists_the_papers(settings):
    recorded = json.loads(json.loads(LEAD_FIXTURE.read_text())[0]["response_json"])
    texts = [s["text"] for s in recorded["sub_questions"]]
    lead = ScriptedClient([("<question>", recorded)])
    qb = ScriptedClient([(f"(sq{i}): ", {"query_string": f"metformin AND topic{i}", "rationale": "in band"})
                         for i in range(1, len(texts) + 1)])
    with run_client(settings, {"lead": lead, "query": qb}) as c:
        r = c.post("/api/runs", json={"question": RUN_Q})
        assert r.status_code == 200, r.text
        body = r.json()
        db = Database(settings.paths.data_dir / "biolitsynth.db")
        assert db.get_run(body["run_id"])["status"] == "stopped_after_dedup"
        detail = c.get(f"/api/runs/{body['run_id']}").json()
        assert [s["text"] for s in detail["plan"]["sub_questions"]] == texts
        assert [s["id"] for s in detail["plan"]["sub_questions"]] == [f"sq{i}" for i in range(1, len(texts) + 1)]
        assert len(detail["queries"]) == len(texts)
        non_cached = [e for e in db.trace_events(body["run_id"]) if e["event_type"] == "llm_call" and not e["cache_hit"]]
        assert body["llm_calls"] == len(non_cached) > 0
        listed = {p["pmid"] for p in c.get(f"/api/runs/{body['run_id']}/papers").json()}
        assert listed == {r_["pmid"] for r_ in db.run_paper_entries(body["run_id"])} and listed
        db.close()


def test_post_run_rejects_a_short_question_without_creating_a_run(settings):
    with run_client(settings, {"lead": ScriptedClient([]), "query": ScriptedClient([])}) as c:
        assert c.post("/api/runs", json={"question": "too short"}).status_code == 422
        db = Database(settings.paths.data_dir / "biolitsynth.db")
        assert db.query("SELECT COUNT(*) FROM runs")[0][0] == 0
        db.close()


def test_post_run_without_api_key_is_400_and_creates_no_run(settings, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with run_client(settings, None) as c:
        r = c.post("/api/runs", json={"question": RUN_Q})
        assert r.status_code == 400 and "cannot start" in r.json()["detail"]
        db = Database(settings.paths.data_dir / "biolitsynth.db")
        assert db.query("SELECT COUNT(*) FROM runs")[0][0] == 0
        db.close()


# ---- BioASQ retrieval eval (dev split only) ----

EV_DRAFT, EV_FINAL = "metformin[majr] AND crc[ti]", "metformin AND colorectal"
EV_X = "41911553"         # relevant, not in the final query's results


def _eval_files(tmp_path, reviewed_by="null", dev="[d1]"):
    src = tmp_path / "bq.json"
    src.write_text(json.dumps({"questions": [
        {"id": "d1", "body": "Does metformin lower colorectal cancer risk?", "type": "yesno",
         "documents": [f"http://www.ncbi.nlm.nih.gov/pubmed/{p}" for p in (CRC_IDS[0], CRC_IDS[1], EV_X)]},
        {"id": "h1", "body": "Held back question that must never run?", "type": "yesno",
         "documents": [f"http://www.ncbi.nlm.nih.gov/pubmed/{CRC_IDS[2]}"]}]}))
    sub = tmp_path / "subset.yaml"
    sub.write_text(f"reviewed_by: {reviewed_by}\ndev: {dev}\nheld_back: [h1]\n")
    return src, sub


def _eval_app(settings, tmp_path, dev="[d1]", **over):
    src, sub = _eval_files(tmp_path, dev=dev)
    ncbi = FakeNcbi(searches={EV_DRAFT: [CRC_IDS[0]], EV_FINAL: CRC_IDS}, totals={EV_DRAFT: 3, EV_FINAL: 150})
    ov = {"bioasq_path": src, "subset_path": sub, "transport": ncbi.transport, "ncbi_env": {},
          "llm_limiter": TokenBucket(10_000), "ncbi_limiter": TokenBucket(10_000),
          "client": ScriptedClient([("colorectal cancer risk?\nFacet", {"query_string": EV_DRAFT, "rationale": "r"}),
                                    ("Broaden it", {"query_string": EV_FINAL, "rationale": "r"})])}
    ov.setdefault("log_path", tmp_path / "eval_log.jsonl")
    ov.update(over)
    return create_app(settings, eval_overrides=ov)


def _run_ids(settings):
    db = Database(settings.paths.data_dir / "biolitsynth.db")
    try:
        return {r["run_id"] for r in db.query("SELECT run_id FROM runs")}
    finally:
        db.close()


def test_post_eval_runs_dev_only_and_the_endpoints_match_the_file(settings, tmp_path):
    with TestClient(_eval_app(settings, tmp_path)) as c:
        r = c.post("/api/evals", json={"limit": 5})
        assert r.status_code == 200, r.text
        body = r.json()
        saved = json.loads((settings.paths.runs_dir / body["eval_id"] / "retrieval.json").read_text())
        detail = c.get(f"/api/evals/{body['eval_id']}").json()
        assert detail["report"] == saved and detail["status"] == "completed_eval"
        assert body["summary"] == saved["summary"] == detail["report"]["summary"]
        assert saved["split"] == "dev" and [q["id"] for q in saved["questions"]] == ["d1"]
        assert "h1" not in json.dumps(saved)
        assert body["eval_id"] not in [x["run_id"] for x in c.get("/api/runs").json()]
        listed = c.get("/api/evals").json()
        assert [e["run_id"] for e in listed] == [body["eval_id"]]
        assert listed[0]["summary"] == saved["summary"] and listed[0]["question"]
        assert c.get("/api/evals/nope").status_code == 404


def test_eval_detail_404_for_a_non_eval_run(client):
    c, s, _, _ = client
    assert c.get(f"/api/evals/{s.run_id}").status_code == 404
    assert c.get("/api/evals").json() == []


@pytest.mark.parametrize("limit", [0, 31])
def test_post_eval_limit_out_of_range_is_422(settings, tmp_path, limit):
    with TestClient(_eval_app(settings, tmp_path)) as c:
        assert c.post("/api/evals", json={"limit": limit}).status_code == 422
        assert _run_ids(settings) == set()


def test_post_eval_missing_subset_is_400_and_writes_no_run(settings, tmp_path):
    with TestClient(_eval_app(settings, tmp_path, subset_path=tmp_path / "missing.yaml")) as c:
        before = _run_ids(settings)
        r = c.post("/api/evals", json={})
        assert r.status_code == 400 and "subset file not found" in r.json()["detail"]
        assert _run_ids(settings) == before


def test_post_eval_unknown_subset_id_is_400(settings, tmp_path):
    with TestClient(_eval_app(settings, tmp_path, dev="[d1, zzz]")) as c:
        assert c.post("/api/evals", json={}).status_code == 400
        assert _run_ids(settings) == set()


def test_post_eval_without_api_key_is_400_and_writes_no_run(settings, tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with TestClient(_eval_app(settings, tmp_path, client=None)) as c:
        before = _run_ids(settings)
        r = c.post("/api/evals", json={"limit": 1})
        assert r.status_code == 400 and r.json()["detail"].startswith("cannot start")
        assert _run_ids(settings) == before


def test_eval_subset_reflects_the_file(settings, tmp_path):
    with TestClient(_eval_app(settings, tmp_path)) as c:
        assert c.get("/api/eval/subset").json() == {
            "source_present": True, "subset_present": True, "reviewed_by": None, "dev": 1, "held_back": 1}
    with TestClient(_eval_app(settings, tmp_path, bioasq_path=tmp_path / "x.json", subset_path=tmp_path / "x.yaml")) as c:
        s = c.get("/api/eval/subset").json()
        assert (s["source_present"], s["subset_present"], s["dev"]) == (False, False, 0)


# ---- committed eval log ----

def test_post_eval_appends_one_log_line_and_the_endpoint_returns_it(settings, tmp_path):
    log = tmp_path / "eval_log.jsonl"
    with TestClient(_eval_app(settings, tmp_path)) as c:
        assert c.get("/api/eval/log").json() == []
        body = c.post("/api/evals", json={"limit": 5}).json()
        lines = log.read_text().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["eval_id"] == body["eval_id"] and rec["summary"] == body["summary"]
        assert rec["question_ids"] == ["d1"] and rec["config_hash"] and "metformin" not in lines[0]
        assert c.get("/api/eval/log").json() == [rec]


def test_failed_eval_writes_no_log_line(settings, tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    log = tmp_path / "eval_log.jsonl"
    with TestClient(_eval_app(settings, tmp_path, client=None)) as c:
        assert c.post("/api/evals", json={"limit": 1}).status_code == 400
    with TestClient(_eval_app(settings, tmp_path, client=ScriptedClient([]))) as c:     # script runs out mid-eval
        assert c.post("/api/evals", json={"limit": 1}).status_code == 502
    assert not log.exists()


def test_eval_log_is_newest_first(settings, tmp_path):
    log = tmp_path / "eval_log.jsonl"
    old = {"eval_id": "eval_1", "date": "2026-01-01T00:00:00+00:00"}
    new = {"eval_id": "eval_2", "date": "2026-02-01T00:00:00+00:00"}
    log.write_text(json.dumps(old) + "\n" + json.dumps(new) + "\n")
    with TestClient(_eval_app(settings, tmp_path)) as c:
        assert [r["eval_id"] for r in c.get("/api/eval/log").json()] == ["eval_2", "eval_1"]


def test_eval_questions_reads_the_local_source_only(settings, tmp_path):
    with TestClient(_eval_app(settings, tmp_path)) as c:
        assert c.get("/api/eval/questions", params={"ids": "d1,zzz"}).json() == {
            "d1": "Does metformin lower colorectal cancer risk?"}
    with TestClient(_eval_app(settings, tmp_path, bioasq_path=tmp_path / "x.json")) as c:
        assert c.get("/api/eval/questions", params={"ids": "d1"}).json() == {}
