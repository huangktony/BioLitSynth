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


def test_live_run_is_refused_for_now(capsys):
    assert main(["run", "q?"]) == 2
