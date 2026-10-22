"""Plan §4.2: planning & retrieval models."""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .enums import Facet


class SubQuestion(BaseModel):
    id: str = Field(pattern=r"^sq\d+$")
    text: str
    facet: Facet
    rationale: str                     # why this complements the others
    key_concepts: list[str]            # must appear in the query (as MeSH or tiab)


class Bridge(BaseModel):
    id: str = Field(pattern=r"^br\d+$")
    from_sub_question_id: str
    to_sub_question_id: str
    hypothesis: str                    # "AMPK activation (sq2) may explain reduced tumor growth (sq1)"


class ResearchPlan(BaseModel):
    run_id: str
    question: str
    effort: Literal["simple", "moderate", "complex"]   # → 2 / 3 / 4 sub-questions
    sub_questions: list[SubQuestion] = Field(min_length=2, max_length=4)
    bridges: list[Bridge] = Field(default=[], max_length=2)   # cross-literature hypotheses (§5.1)
    out_of_scope_notes: list[str] = []

    @model_validator(mode="after")
    def _cross_field_rules(self):
        want = {"simple": 2, "moderate": 3, "complex": 4}[self.effort]
        if len(self.sub_questions) != want:
            raise ValueError(f"effort={self.effort!r} means {want} sub-questions, got {len(self.sub_questions)}")
        ids = [s.id for s in self.sub_questions]
        if len(set(ids)) != len(ids):
            raise ValueError("sub-question ids must be unique")
        if len({b.id for b in self.bridges}) != len(self.bridges):
            raise ValueError("bridge ids must be unique")
        for b in self.bridges:
            for end in (b.from_sub_question_id, b.to_sub_question_id):
                if end not in ids:
                    raise ValueError(f"bridge {b.id} points at unknown sub-question {end!r}")
        return self


class CoverageGap(BaseModel):
    sub_question_id: str
    reason: str
    suggested_new_concepts: list[str] = []


class CoverageDecision(BaseModel):
    sufficient: bool
    gaps: list[CoverageGap] = []


class QueryFilters(BaseModel):
    min_year: int | None = None
    max_year: int | None = None
    humans_only: bool = False
    article_types: list[str] = []      # e.g. ["Randomized Controlled Trial", "Meta-Analysis"]
    english_only: bool = True


class PubMedQuery(BaseModel):
    id: str = Field(pattern=r"^q\d+$")
    sub_question_id: str
    query_string: str                  # final PubMed syntax incl. [MeSH]/[tiab]
    filters: QueryFilters = QueryFilters()
    hit_count: int | None = None
    refinement_history: list[tuple[str, int]] = []   # (query_string, hit_count)
    retmax_candidates: int = 100       # fetched from PubMed (relevance sort)
    keep_top: int = 25                 # top-X kept after MedCPT rerank
    sort: Literal["relevance", "pub_date"] = "relevance"
