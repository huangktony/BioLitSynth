import yaml

from biolitsynth.cli import main

from ..conftest import REPO


def test_dry_run_via_cli(tmp_path, capsys):
    raw = yaml.safe_load((REPO / "config.yaml").read_text())
    raw["paths"] = {"data_dir": str(tmp_path / "data"), "runs_dir": str(tmp_path / "runs")}
    cfg = tmp_path / "c.yaml"
    cfg.write_text(yaml.safe_dump(raw))
    assert main(["run", "q?", "--dry", "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "gap rounds=1" in out and "synth attempts=2" in out and "assembled" in out


def test_live_run_without_a_key_is_refused_before_anything_runs(tmp_path, capsys):
    raw = yaml.safe_load((REPO / "config.yaml").read_text())
    raw["paths"] = {"data_dir": str(tmp_path / "data"), "runs_dir": str(tmp_path / "runs")}
    cfg = tmp_path / "c.yaml"
    cfg.write_text(yaml.safe_dump(raw))
    assert main(["run", "q?", "--config", str(cfg)]) == 2
    assert "GEMINI_API_KEY" in capsys.readouterr().err and not (tmp_path / "data").exists()


def test_wrong_number_of_sub_questions_is_refused(capsys):
    assert main(["run", "q?", "--sub-question", "only one"]) == 2


def test_eval_retrieval_appends_to_the_log_and_backfill_is_idempotent(settings, tmp_path, monkeypatch, capsys):
    import json
    from biolitsynth import cli
    from biolitsynth.eval import retrieval
    from biolitsynth.pubmed import TokenBucket
    from ..integration.test_api import EV_DRAFT, EV_FINAL, _eval_files
    from ..llm_mock import ScriptedClient
    from ..pubmed_mock import CRC_IDS, FakeNcbi

    src, sub = _eval_files(tmp_path)
    ncbi = FakeNcbi(searches={EV_DRAFT: [CRC_IDS[0]], EV_FINAL: CRC_IDS}, totals={EV_DRAFT: 3, EV_FINAL: 150})
    client = ScriptedClient([("colorectal cancer risk?\nFacet", {"query_string": EV_DRAFT, "rationale": "r"}),
                             ("Broaden it", {"query_string": EV_FINAL, "rationale": "r"})])
    real = retrieval.run_retrieval_eval
    monkeypatch.setattr(retrieval, "run_retrieval_eval", lambda *a, **k: real(
        *a, **k, transport=ncbi.transport, ncbi_env={}, llm_limiter=TokenBucket(10_000), ncbi_limiter=TokenBucket(10_000)))
    monkeypatch.setattr("biolitsynth.graph.runner.resolve_clients", lambda *a, **k: {"query": client})
    monkeypatch.setattr(cli, "load_settings", lambda _=None: settings)
    log = tmp_path / "log.jsonl"
    assert cli.main(["eval", "retrieval", "--bioasq", str(src), "--subset", str(sub), "--log", str(log)]) == 0
    lines = log.read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["question_ids"] == ["d1"]
    assert "logged to" in capsys.readouterr().out
    assert cli.main(["eval", "backfill-log", "--log", str(log)]) == 0      # already logged: nothing appended
    assert len(log.read_text().splitlines()) == 1
    log.unlink()
    assert cli.main(["eval", "backfill-log", "--log", str(log)]) == 0
    assert json.loads(log.read_text())["backfilled"] is True

