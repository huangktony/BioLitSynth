"""Plan §4.1."""
from enum import Enum


class Facet(str, Enum):
    MECHANISM = "mechanism"
    CLINICAL_OUTCOME = "clinical_outcome"
    EPIDEMIOLOGY = "epidemiology"
    SAFETY = "safety"
    POPULATION_MODIFIER = "population_modifier"
    OTHER = "other"


class StudyDesign(str, Enum):
    META_ANALYSIS = "meta_analysis"
    SYSTEMATIC_REVIEW = "systematic_review"
    RCT = "rct"
    COHORT = "cohort"
    CASE_CONTROL = "case_control"
    CROSS_SECTIONAL = "cross_sectional"
    CASE_REPORT = "case_report"
    ANIMAL = "animal"
    IN_VITRO = "in_vitro"
    COMPUTATIONAL = "computational"
    NARRATIVE_REVIEW = "narrative_review"
    OTHER = "other"


class Organism(str, Enum):
    HUMAN = "human"
    ANIMAL = "animal"
    CELL_LINE = "cell_line"
    NOT_APPLICABLE = "n/a"


class Direction(str, Enum):
    INCREASES = "increases"
    DECREASES = "decreases"
    NO_EFFECT = "no_effect"
    ASSOCIATED = "associated"          # non-directional association
    NOT_ASSOCIATED = "not_associated"
    MIXED = "mixed"


class ClaimType(str, Enum):
    DIRECT = "direct"        # explicitly stated by ≥1 paper
    INFERRED = "inferred"    # derived by combining ≥2 evidence items
    CONTESTED = "contested"  # credible evidence on both sides
    GAP = "gap"              # question asked, literature retrieved does not answer


class Support(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"


class VerdictStatus(str, Enum):
    PASS = "pass"
    FLAG = "flag"   # shipped with a visible warning
    FAIL = "fail"   # sent back to synthesizer / dropped
