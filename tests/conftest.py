from pathlib import Path

import pytest

from biolitsynth.config import Paths, load_settings

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Tests are offline: ignore .env and hide any real keys exported in the shell."""
    monkeypatch.setenv("BIOLITSYNTH_NO_DOTENV", "1")
    for name in ("GEMINI_API_KEY", "NCBI_API_KEY", "NCBI_EMAIL", "NVIDIA_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings(tmp_path):
    """Real config.yaml, but all output goes to tmp_path."""
    s = load_settings(REPO / "config.yaml")
    s.paths = Paths(data_dir=tmp_path / "data", runs_dir=tmp_path / "runs")
    return s
