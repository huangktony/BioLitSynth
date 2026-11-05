"""Lead Agent, Call 1: question → ResearchPlan (plan §5.1, Appendix A).

The model returns a PlanDraft. Code assigns sub-question ids (sq1…), derives `effort` from how many sub-questions
there are, enforces the configured maximum, and checks every bridge. Problems the plan treats as quality signals
(repeated facets, too few or too many key concepts) are returned as warnings for the trace, not errors.
The coverage call (Call 2) arrives in P4.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..llm import StageLLM, load_prompt
from ..schemas import Bridge, PlanDraft, ResearchPlan, SubQuestion

PROMPT = "lead"
EFFORT = {2: "simple", 3: "moderate", 4: "complex"}
MAX_BRIDGES = 2


class InvalidPlan(ValueError):
    pass


@dataclass
class BuiltPlan:
    plan: ResearchPlan
    warnings: list[str] = field(default_factory=list)


def build_plan(run_id: str, question: str, draft: PlanDraft, *, max_sub_questions: int) -> BuiltPlan:
    warnings: list[str] = []
    sqs = [s for s in draft.sub_questions if s.text.strip()]
    if len(sqs) < len(draft.sub_questions):
        warnings.append(f"lead.empty_sub_question dropped {len(draft.sub_questions) - len(sqs)}")
    if len(sqs) > max_sub_questions:
        warnings.append(f"lead.too_many_sub_questions kept {max_sub_questions} of {len(sqs)}")
        sqs = sqs[:max_sub_questions]
    if len(sqs) < 2:
        raise InvalidPlan(f"the plan needs at least 2 sub-questions, the model returned {len(sqs)}")

    sub_questions = []
    for i, s in enumerate(sqs, 1):
        concepts = list(dict.fromkeys(c.strip() for c in s.key_concepts if c.strip()))
        if not 2 <= len(concepts) <= 5:
            warnings.append(f"lead.key_concepts sq{i} has {len(concepts)} (rule: 2-5)")
        sub_questions.append(SubQuestion(id=f"sq{i}", text=s.text.strip(), facet=s.facet,
                                         rationale=s.rationale.strip(), key_concepts=concepts))
    facets = [s.facet for s in sub_questions]
    if len(set(facets)) < len(facets):
        warnings.append("lead.repeated_facet " + ", ".join(f"{s.id}={s.facet.value}" for s in sub_questions))

    bridges, seen = [], set()
    for b in draft.bridges:
        ends = (b.from_sub_question, b.to_sub_question)
        if not all(1 <= e <= len(sub_questions) for e in ends) or ends[0] == ends[1]:
            warnings.append(f"lead.bridge_dropped {ends[0]}->{ends[1]} (no such pair of sub-questions)")
        elif not b.hypothesis.strip() or ends in seen:
            warnings.append(f"lead.bridge_dropped {ends[0]}->{ends[1]} (empty or repeated)")
        elif len(bridges) >= MAX_BRIDGES:
            warnings.append(f"lead.bridge_dropped {ends[0]}->{ends[1]} (more than {MAX_BRIDGES})")
        else:
            seen.add(ends)
            bridges.append(Bridge(id=f"br{len(bridges) + 1}", from_sub_question_id=f"sq{ends[0]}",
                                  to_sub_question_id=f"sq{ends[1]}", hypothesis=b.hypothesis.strip()))

    notes = [n.strip() for n in draft.out_of_scope_notes if n.strip()]
    plan = ResearchPlan(run_id=run_id, question=question, effort=EFFORT[len(sub_questions)],
                        sub_questions=sub_questions, bridges=bridges, out_of_scope_notes=notes)
    return BuiltPlan(plan, warnings)


async def plan_question(run_id: str, question: str, llm: StageLLM, *, max_sub_questions: int,
                        parent: str | None = None) -> BuiltPlan:
    if not 2 <= max_sub_questions <= 4:
        raise ValueError("max_sub_questions must be 2 to 4 (plan §1.3)")
    prompt = load_prompt(PROMPT).render("plan", question=question, max_sub_questions=max_sub_questions)
    draft = await llm.complete(prompt, PlanDraft, input_refs=[run_id], parent=parent)
    return build_plan(run_id, question, draft, max_sub_questions=max_sub_questions)
