from pathlib import Path

import pytest

from biolitsynth.config import Paths, load_settings

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def settings(tmp_path):
    """Real config.yaml, but all output goes to tmp_path."""
    s = load_settings(REPO / "config.yaml")
    s.paths = Paths(data_dir=tmp_path / "data", runs_dir=tmp_path / "runs")
    return s
