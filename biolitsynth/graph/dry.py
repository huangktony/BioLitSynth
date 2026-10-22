"""Stage stubs for P0. Each later phase replaces one of these with a real agent in biolitsynth/agents/.

Everything here is deterministic and needs no network. The `dry_force_*` switches make each bounded loop
fire exactly once so a dry run proves the loops, their counters and their caps.
"""
from __future__ import annotations

from ..schemas import Claim, ClaimType, Facet, ResearchPlan, SubQuestion

_FACETS = [Facet.CLINICAL_OUTCOME, Facet.MECHANISM, Facet.EPIDEMIOLOGY, Facet.SAFETY]
_EFFORT = {2: "simple", 3: "moderate", 4: "complex"}


def dry_plan(run_id: str, question: str, n: int) -> ResearchPlan:
    return ResearchPlan(
        run_id=run_id, question=question, effort=_EFFORT[n],
        sub_questions=[
            SubQuestion(id=f"sq{i + 1}", text=f"[dry] {_FACETS[i].value} angle on: {question}",
                        facet=_FACETS[i], rationale="[dry] placeholder", key_concepts=[])
            for i in range(n)
        ],
    )


def dry_hit_count(refinements: int, force_refine: bool) -> int:
    """First count is out of band (0) when forcing a refinement, then in band."""
    return 0 if force_refine and refinements == 0 else 150


def dry_pmids(query_id: str, per_query: int = 4) -> list[str]:
    """Synthetic PMIDs (never written to the papers table). 12 over 3 queries exercises 5-per-batch batching."""
    n = int(query_id.removeprefix("q"))
    return [str(900_000_000 + n * 100 + i) for i in range(per_query)]


def dry_gaps(sub_question_ids: list[str], force: bool) -> list[str]:
    """Gaps the Lead reports. Like the real Lead (Appendix A), it keeps reporting them after the round cap,
    so the coverage node, not this stub, has to enforce the cap."""
    return sub_question_ids[1:2] if force else []


def dry_claims(attempt: int, redo: list[str] | None, sub_question_ids: list[str]) -> list[Claim]:
    """Attempt 0: one GAP claim per sub-question (GAP needs no evidence, so the stub stays honest).
    Later attempts regenerate only `redo`; passed claims are frozen."""
    sqs = sub_question_ids[:2] or ["sq1"]
    claims = [
        Claim(id=f"cl{i + 1}", text=f"[dry] placeholder claim (attempt {attempt})", claim_type=ClaimType.GAP,
              sub_question_ids=[sq], evidence_ids=[], confidence="low")
        for i, sq in enumerate(sqs)
    ]
    return claims if redo is None else [c for c in claims if c.id in redo]
