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
