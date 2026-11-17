"""LLM-facing draft schemas (OPEN_QUESTIONS: "*Draft schemas").

A draft holds only what the model may decide. Code owns ids, hit counts and refinement history and builds the full
§4 model from the draft. Drafts avoid constructs that providers' structured-output modes reject: no tuples, no regex
`pattern`, no model validators.
"""
from pydantic import BaseModel, Field

from .enums import Direction, Facet, Organism, StudyDesign


class QueryDraft(BaseModel):
    """One PubMed query proposed by the Query Builder (plan §5.2)."""
    query_string: str = Field(description="Complete PubMed search string, using [MeSH Terms], [tiab] and boolean "
                                          "grouping. Filters go inside the string.")
    rationale: str = Field(description="One sentence: what this query covers or what was changed and why.")


class SubQuestionDraft(BaseModel):
    text: str = Field(description="The sub-question: names the exposure, the outcome and, if relevant, the population.")
    facet: Facet
    rationale: str = Field(description="One sentence: what this sub-question adds that the others don't.")
    key_concepts: list[str] = Field(description="2 to 5 concepts a PubMed query must include. Terms, not query syntax.")


class BridgeDraft(BaseModel):
    from_sub_question: int = Field(description="1-based position of the sub-question the hypothesis starts from.")
    to_sub_question: int = Field(description="1-based position of the sub-question it links to.")
    hypothesis: str


class PlanDraft(BaseModel):
    """The Lead's plan call (plan §5.1). Code assigns ids, derives `effort` from the count, and builds ResearchPlan."""
    sub_questions: list[SubQuestionDraft]
    bridges: list[BridgeDraft]
    out_of_scope_notes: list[str]


class EvidenceDraft(BaseModel):
    """One finding from the Evidence Extractor (plan §5.4). Code adds ids, offsets, strength and sub-question links."""
    pmid: str = Field(description="The pmid of the abstract this finding comes from, exactly as given.")
    statement: str = Field(description="The finding in one plain sentence, no more than the quote supports.")
    quote: str = Field(description="Copied character for character from the abstract: one sentence or part of one, "
                                   "20 to 600 characters.")
    subject: str = Field(description="The exposure or intervention, short, as written in the abstract.")
    outcome: str = Field(description="The outcome measured, short, as written in the abstract.")
    direction: Direction
    effect_size: str | None = Field(default=None, description="Effect size copied verbatim from the abstract, or null.")
    population: str | None = Field(default=None, description="Who was studied, short, or null.")
    organism: Organism
    study_design: StudyDesign
    sample_size: int | None = Field(default=None, description="Number of participants as written in the abstract, "
                                                              "or null. Never computed.")
    cohort_or_trial_name: str | None = Field(default=None, description="Named cohort or trial copied verbatim from "
                                                                      "the abstract, or null.")
    limitations: list[str] = Field(default=[], description="Limitations the abstract itself states, if any.")


class ExtractionDraft(BaseModel):
    """The extractor's whole reply for one call."""
    findings: list[EvidenceDraft] = Field(description="1 to 5 findings per abstract; none for an abstract with no "
                                                      "relevant result.")
