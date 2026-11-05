"""LLM-facing draft schemas (OPEN_QUESTIONS: "*Draft schemas").

A draft holds only what the model may decide. Code owns ids, hit counts and refinement history and builds the full
§4 model from the draft. Drafts avoid constructs that providers' structured-output modes reject: no tuples, no regex
`pattern`, no model validators.
"""
from pydantic import BaseModel, Field


class QueryDraft(BaseModel):
    """One PubMed query proposed by the Query Builder (plan §5.2)."""
    query_string: str = Field(description="Complete PubMed search string, using [MeSH Terms], [tiab] and boolean "
                                          "grouping. Filters go inside the string.")
    rationale: str = Field(description="One sentence: what this query covers or what was changed and why.")
