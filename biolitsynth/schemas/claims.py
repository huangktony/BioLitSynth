"""Plan §4.5: claims, citations, report."""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .enums import ClaimType


class ReasoningStep(BaseModel):
    premise_evidence_ids: list[str] = Field(min_length=1)
    inference: str


class Claim(BaseModel):
    id: str = Field(pattern=r"^cl\d+$")
    text: str
    claim_type: ClaimType
    sub_question_ids: list[str]
    evidence_ids: list[str]
    conflict_id: str | None = None
    reasoning: list[ReasoningStep] = []
    confidence: Literal["high", "moderate", "low"]
    caveats: list[str] = []

    @model_validator(mode="after")
    def _type_rules(self):
        t = self.claim_type
        if t != ClaimType.GAP and not self.evidence_ids:
            raise ValueError("non-gap claims need evidence_ids")
        if t == ClaimType.INFERRED:
            if len(self.evidence_ids) < 2 or not self.reasoning:
                raise ValueError("inferred claims need ≥2 evidence ids and reasoning steps")
        if t == ClaimType.CONTESTED and not self.conflict_id:
            raise ValueError("contested claims must reference a conflict")
        return self


class Citation(BaseModel):
    evidence_id: str
    pmid: str
    quote: str                         # copied from store by code, never by LLM


class CitedSentence(BaseModel):
    text: str
    claim_ids: list[str]
    citations: list[Citation]          # resolved by code from claim → evidence


class CitedReport(BaseModel):
    run_id: str
    question: str
    sections: dict[str, list[CitedSentence]]   # keyed by sub-question id + "overall"
    claims: list[Claim]
    uncited_sentences: list[str]       # Citation Agent output, must be empty to pass
