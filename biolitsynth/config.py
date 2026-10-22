"""Typed loader for config.yaml. Unknown keys are rejected so typos fail at startup, not mid-run."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelRef(_Strict):
    provider: str
    model: str


class Models(_Strict):
    lead: ModelRef
    query: ModelRef
    extractor: ModelRef
    normalizer: ModelRef
    conflict: ModelRef
    synthesizer: ModelRef
    citation: ModelRef
    critic: ModelRef


class Limits(_Strict):
    """Hard limits from plan §1.3. `le` is the documented max."""
    sub_questions: int = Field(3, ge=2, le=4)
    retmax_candidates: int = Field(100, ge=1, le=200)
    papers_per_query: int = Field(25, ge=1, le=50)
    coverage_extra_rounds: int = Field(1, ge=0, le=1)
    query_refinement_attempts: int = Field(2, ge=0, le=2)
    synth_retries: int = Field(2, ge=0, le=2)
    abstracts_per_extraction: int = Field(5, ge=1, le=8)


class QueryBand(_Strict):
    min_hits: int = 20
    max_hits: int = 2000


class ConflictThresholds(_Strict):
    balance_ratio: float = 2
    lopsided_ratio: float = 4


class Concurrency(_Strict):
    ncbi: int = Field(5, ge=1)
    llm: int = Field(4, ge=1)


class Budget(_Strict):
    max_llm_calls_per_run: int = 60
    max_tokens_per_run: int | None = None


class GraphConfig(_Strict):
    recursion_limit: int = 60


class Paths(_Strict):
    data_dir: Path = Path("data")
    runs_dir: Path = Path("runs")


class Settings(_Strict):
    models: Models
    limits: Limits = Limits()
    query_band: QueryBand = QueryBand()
    conflict: ConflictThresholds = ConflictThresholds()
    concurrency: Concurrency = Concurrency()
    budget: Budget = Budget()
    graph: GraphConfig = GraphConfig()
    paths: Paths = Paths()


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    """Load settings from `path`, else $BIOLITSYNTH_CONFIG, else ./config.yaml."""
    p = Path(path or os.environ.get("BIOLITSYNTH_CONFIG") or "config.yaml")
    return Settings.model_validate(yaml.safe_load(p.read_text()))
