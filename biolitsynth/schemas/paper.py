"""Plan §4.2: papers, relevance scores, study clusters."""
from typing import Literal

from pydantic import BaseModel, Field


class RelevanceScore(BaseModel):       # produced by the MedCPT reranker (code + local model)
    sub_question_id: str
    pmid: str
    score: float                       # raw cross-encoder logit (uncalibrated; compare within a sub-question only)
    rank: int                          # 1 = most relevant for this sub-question
    pubmed_rank: int                   # original esearch position, kept for comparison
    kept: bool                         # in top-X and ≥ min_score
    drop_reason: Literal["below_top_x", "below_min_score"] | None = None
    model: str                         # e.g. "ncbi/MedCPT-Cross-Encoder"


class Paper(BaseModel):
    pmid: str = Field(pattern=r"^\d{1,9}$")
    title: str
    abstract: str                      # structured sections joined as "LABEL: text\n"
    abstract_sha256: str               # computed in code; detects drift
    authors: list[str]
    journal: str | None = None
    year: int | None = None
    doi: str | None = None
    publication_types: list[str]       # from PubMed, used to seed study_design
    mesh_terms: list[str]
    retrieved_by: list[str]            # query ids, e.g. ["q1", "q3"]
    rank_by_query: dict[str, int]      # retrieval rank in each query
    registry_ids: list[str] = []       # e.g. ["NCT01234567"], from PubMed DataBankList + abstract regex (code)
    is_retracted: bool = False         # PubMed pub type "Retracted Publication" (code)
    study_id: str | None = None        # set by Study Clusterer, e.g. "st7"
    relevance: list[RelevanceScore] = []   # one per sub-question it was a candidate for


class StudyCluster(BaseModel):
    """One independent study. Multiple PMIDs (primary report, secondary analysis,
    duplicate publication) may belong to it. All 'how many sources' rules count studies, not PMIDs."""
    id: str = Field(pattern=r"^st\d+$")
    pmids: list[str] = Field(min_length=1)
    primary_pmid: str                  # earliest / largest-n paper in the cluster
    link_reasons: list[Literal[
        "same_registry_id",            # identical NCT/ISRCTN/etc.
        "same_named_cohort",           # e.g. both "UK Biobank"
        "author_n_year_match",         # same first author + same n (±5%) + year within 2
        "pubmed_duplicate_flag",       # pub type "Duplicate Publication"
    ]] = []
    confidence: Literal["certain", "likely"]   # registry match = certain; heuristics = likely
