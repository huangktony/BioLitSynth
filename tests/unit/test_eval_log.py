"""The committed eval log: record fields, hashing, append rules, backfill. Built from the offline retrieval eval
(ScriptedClient + FakeNcbi); the synthetic question bodies and PMIDs must never appear in a record."""
import json

import pytest

from biolitsynth.eval.log import (append_log, config_hash, read_log, record_from_report,
                                  record_from_retrieval_json)
from biolitsynth.eval.retrieval import run_retrieval_eval
from biolitsynth.pubmed import TokenBucket

from ..llm_mock import ScriptedClient
from ..pubmed_mock import FakeNcbi
from ..integration.test_eval_retrieval import DRAFT, FINAL, INBAND, PROBE, Q1, Q2, CRC_IDS


async def _report(settings, tmp_path):
    ncbi = FakeNcbi(searches={DRAFT: [CRC_IDS[0]], FINAL: CRC_IDS, PROBE: [CRC_IDS[0]], INBAND: CRC_IDS[:3]},
                    totals={DRAFT: 3, FINAL: 150, INBAND: 90})
    client = ScriptedClient([
        ("colorectal cancer risk?\nFacet", {"query_string": DRAFT, "rationale": "r"}),
        ("Broaden it", {"query_string": FINAL, "rationale": "r"}),
        ("protect the liver?", {"query_string": INBAND, "rationale": "r"}),
    ])
    return await run_retrieval_eval([Q1, Q2], settings, split="dev", client=client, transport=ncbi.transport,
                                    ncbi_env={}, llm_limiter=TokenBucket(10_000), ncbi_limiter=TokenBucket(10_000),
                                    out_dir=tmp_path / "out")


CFG = json.dumps({"b": 1, "a": {"y": 2, "x": [1, 2]}})


async def test_record_fields_and_no_question_text_or_pmids(settings, tmp_path):
    report, _ = await _report(settings, tmp_path)
    rec = record_from_report(report, settings, config_json=CFG)
    assert rec["eval_id"] == report.eval_id and rec["split"] == "dev" and rec["n_questions"] == 2
    assert rec["question_ids"] == ["bq1", "bq2"] and rec["retmax"] == report.retmax
    assert rec["model"] == report.model and rec["prompt_version"] == "query_builder.v1"
    assert len(rec["prompt_hash"]) == 64 and rec["config_hash"] == config_hash(CFG) and len(rec["config_hash"]) == 12
    assert rec["date"].endswith("+00:00") and "backfilled" not in rec
    assert rec["git_dirty"] in (True, False, None)
    assert rec["summary"] == report.summary()
    q1 = report.questions[0]
    assert rec["per_question"]["bq1"] == {
        "recall_fetch": q1.recall_fetch, "recall_fetch_draft": q1.recall_fetch_draft, "refinements": 1,
        "in_band": True, "misses_beyond_cap": list(q1.misses.values()).count("beyond_cap"),
        "misses_not_matched": list(q1.misses.values()).count("not_matched")}
    blob = json.dumps(rec)
    for q in (Q1, Q2):
        assert q.body not in blob
        assert not any(p in blob for p in q.relevant_pmids)
    assert DRAFT not in blob and FINAL not in blob


def test_config_hash_is_stable_under_key_order():
    a = json.dumps({"x": 1, "y": {"b": 1, "a": 2}})
    b = json.dumps({"y": {"a": 2, "b": 1}, "x": 1})
    assert config_hash(a) == config_hash(b) != config_hash(json.dumps({"x": 2, "y": {"a": 2, "b": 1}}))


async def test_append_refuses_a_duplicate_eval_id(settings, tmp_path):
    report, _ = await _report(settings, tmp_path)
    rec = record_from_report(report, settings, config_json=CFG)
    log = tmp_path / "sub" / "log.jsonl"
    append_log(rec, log)
    with pytest.raises(ValueError):
        append_log(rec, log)
    assert len(log.read_text().splitlines()) == 1 and read_log(log) == [rec]
    assert read_log(tmp_path / "missing.jsonl") == []


async def test_backfill_record_round_trips_the_written_json(settings, tmp_path):
    report, path = await _report(settings, tmp_path)
    live = record_from_report(report, settings, config_json=CFG)
    back = record_from_retrieval_json(path, CFG)
    assert back["backfilled"] is True and back["git_commit"] is None and back["git_dirty"] is None
    for k in ("eval_id", "split", "n_questions", "question_ids", "model", "prompt_version", "prompt_hash",
              "config_hash", "retmax", "summary", "per_question"):
        assert back[k] == live[k], k
    assert back["date"].startswith(report.eval_id[5:9])
    blob = json.dumps(back)
    assert Q1.body not in blob and not any(p in blob for p in Q1.relevant_pmids)


def _rec_for(version):
    from biolitsynth.eval.log import _record
    return _record(eval_id="e", date=None, split="dev", model="m", prompt_version=version, retmax=1, summary={},
                   questions=[], config_json=CFG, git_commit=None, git_dirty=None)


def test_prompt_hash_is_of_the_version_that_ran():
    from biolitsynth.llm import load_prompt
    h1 = load_prompt("query_builder", version=1).hash
    h2 = load_prompt("query_builder", version=2).hash
    assert h1 != h2
    assert _rec_for("query_builder.v1")["prompt_hash"] == h1
    assert _rec_for("query_builder.v2")["prompt_hash"] == h2


@pytest.mark.parametrize("version", ["query_builder.v99", "garbage"])
def test_prompt_hash_none_when_version_unknown(version):
    assert _rec_for(version)["prompt_hash"] is None
