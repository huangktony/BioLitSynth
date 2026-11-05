"""LLM-facing draft schemas (OPEN_QUESTIONS: "*Draft schemas").

A draft holds only what the model may decide. Code owns ids, hit counts and refinement history and builds the full
§4 model from the draft. Drafts avoid constructs that providers' structured-output modes reject: no tuples, no regex
`pattern`, no model validators.
"""
from pydantic import BaseModel, Field

from .enums import Facet


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
