"""Plan §4.3 and §4.4: evidence, term map, conflicts."""
from typing import Literal

from pydantic import BaseModel, Field

from .enums import Direction, Organism, StudyDesign


class EvidenceItem(BaseModel):
    id: str = Field(pattern=r"^ev_\d+_\d+$")   # f"ev_{pmid}_{n}", assigned by code
    pmid: str
    sub_question_ids: list[str]
    statement: str                     # one atomic finding, paraphrased
    quote: str                         # VERBATIM substring of abstract (LLM-provided)
    quote_start: int | None = None     # filled by code after span check
    quote_end: int | None = None
    subject: str                       # as extracted, lowercase: "metformin"
    outcome: str                       # as extracted, lowercase: "crc incidence"
    canonical_subject: str | None = None   # set by code from TermMap
    canonical_outcome: str | None = None   # set by code from TermMap
    cohort_or_trial_name: str | None = None  # verbatim from abstract, e.g. "UKPDS"
    study_id: str | None = None        # copied from Paper by code
    direction: Direction
    effect_size: str | None = None     # verbatim, e.g. "HR 0.83 (95% CI 0.74–0.93)"
    population: str | None = None      # "adults with T2D, n=12,000"
    organism: Organism
    study_design: StudyDesign
    sample_size: int | None = None
    strength: Literal["high", "moderate", "low"] | None = None   # computed by code, NOT LLM
    limitations: list[str] = []
    extractor_model: str
    prompt_version: str


class TermMap(BaseModel):
    """One LLM call per run. Maps every raw subject/outcome string to a canonical key.
    Code applies it. The LLM never edits EvidenceItems directly."""
    subjects: dict[str, str]
    outcomes: dict[str, str]
    unmapped: list[str] = []           # strings the model wasn't confident about → kept as their own key


class SideSummary(BaseModel):          # computed by code
    position: Direction
    evidence_ids: list[str] = Field(min_length=1)
    study_ids: list[str]               # independent studies on this side
    weight: float                      # Σ over studies of max strength score (§5.6)
    best_design: StudyDesign
    organisms: list[Organism]


class ConflictExplanation(BaseModel):
    text: str
    moderator: Literal["organism", "design", "population", "dose", "follow_up", "outcome_definition", "other"]
    evidence_ids: list[str]            # must reference items in the conflict, from BOTH sides


class Conflict(BaseModel):
    id: str = Field(pattern=r"^cf\d+$")
    canonical_subject: str
    canonical_outcome: str
    sides: list[SideSummary] = Field(min_length=2)       # usually 2; 3 if increases / decreases / no_effect
    conflict_type: Literal["direction", "magnitude"]
    balance: Literal["balanced", "leans", "lopsided"]   # computed by code from weights (§5.6), never by LLM
    leaning_side: Direction | None = None               # set only when balance != "balanced"
    status: Literal["moderator_explained", "unresolved"] # LLM adjudication, must cite both sides
    explanations: list[ConflictExplanation] = []
    adjudicator_model: str
