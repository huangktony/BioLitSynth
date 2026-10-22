"""Plan §13 rule 1: LangGraph is the only orchestration framework; no LangChain."""
import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "biolitsynth"


def test_no_langchain_imports():
    pat = re.compile(r"^\s*(from|import)\s+langchain", re.M)
    offenders = [str(p) for p in PKG.rglob("*.py") if pat.search(p.read_text())]
    assert not offenders, offenders
