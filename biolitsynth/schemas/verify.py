"""Plan §4.6: verification models."""
from typing import Literal

from pydantic import BaseModel

from .enums import ClaimType, Support, VerdictStatus


class CheckResult(BaseModel):          # deterministic
    claim_id: str
    check: str                         # "evidence_exists", "quote_in_abstract", ...
    passed: bool
    detail: str | None = None


class EvidenceJudgment(BaseModel):
    evidence_id: str
    support: Support
    rationale: str                     # ≤2 sentences


class OmittedEvidence(BaseModel):      # produced by the cherry-pick scan (code), judged by the Critic
    claim_id: str
    evidence_id: str
    reason: Literal["opposite_direction", "same_topic_uncited_stronger"]
    weight: float                      # strength score of its study
    critic_judgment: Literal["material", "immaterial"] | None = None
    critic_rationale: str | None = None


class Verdict(BaseModel):              # LLM critic + router
    claim_id: str
    per_evidence: list[EvidenceJudgment]
    overall_support: Support
    label_correct: bool
    suggested_label: ClaimType | None = None
    scope_issues: list[Literal[
        "species_extrapolation",        # mouse result stated as human fact
        "causal_from_observational",    # "causes" from a cohort study
        "population_overgeneralized",
        "effect_overstated",
        "hedging_missing",
        "conflict_ignored",
        "false_resolution",             # picks a winner when the conflict is "balanced"
        "omitted_counterevidence",      # uncited, same-topic evidence points the other way
        "non_independent_sources",      # "multiple studies" that are one study published twice
    ]] = []
    status: VerdictStatus
    reasons: list[str]
    critic_model: str
    attempt: int
