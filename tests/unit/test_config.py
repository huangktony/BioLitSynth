from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from biolitsynth.config import Settings, load_settings

CONFIG = Path(__file__).resolve().parents[2] / "config.yaml"


def test_repo_config_loads_with_plan_defaults():
    s = load_settings(CONFIG)
    assert s.limits.sub_questions == 3
    assert s.limits.synth_retries == 2
    assert (s.query_band.min_hits, s.query_band.max_hits) == (20, 2000)
    assert s.graph.recursion_limit == 60


def test_unknown_key_rejected():
    raw = yaml.safe_load(CONFIG.read_text())
    raw["limits"]["sub_questons"] = 3          # typo
    with pytest.raises(ValidationError):
        Settings.model_validate(raw)


@pytest.mark.parametrize("key,value", [("sub_questions", 5), ("papers_per_query", 51), ("synth_retries", 3),
                                       ("abstracts_per_extraction", 9), ("coverage_extra_rounds", 2),
                                       ("retmax_candidates", 201)])
def test_limits_above_plan_max_rejected(key, value):
    raw = yaml.safe_load(CONFIG.read_text())
    raw["limits"][key] = value
    with pytest.raises(ValidationError):
        Settings.model_validate(raw)
