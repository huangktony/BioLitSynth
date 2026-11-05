"""Lead plan call (plan §5.1): code owns ids, effort, the sub-question cap and bridge checks."""
import pytest

from biolitsynth.agents.lead import InvalidPlan, build_plan
from biolitsynth.llm import load_prompt
from biolitsynth.schemas import Facet, PlanDraft


def sq(text, facet="clinical_outcome", concepts=("a", "b")):
    return {"text": text, "facet": facet, "rationale": "why", "key_concepts": list(concepts)}


def draft(sqs, bridges=(), notes=()):
    return PlanDraft.model_validate({"sub_questions": sqs, "bridges": list(bridges), "out_of_scope_notes": list(notes)})


def test_ids_effort_and_bridges_come_from_code():
    d = draft([sq("A?"), sq("B?", "mechanism"), sq("C?", "epidemiology")],
              [{"from_sub_question": 2, "to_sub_question": 1, "hypothesis": " h "}], [" note ", " "])
    b = build_plan("r1", "Q?", d, max_sub_questions=4)
    p = b.plan
    assert (p.run_id, p.question, p.effort) == ("r1", "Q?", "moderate")
    assert [s.id for s in p.sub_questions] == ["sq1", "sq2", "sq3"]
    assert [(x.id, x.from_sub_question_id, x.to_sub_question_id, x.hypothesis) for x in p.bridges] == \
        [("br1", "sq2", "sq1", "h")]
    assert p.out_of_scope_notes == ["note"] and b.warnings == []


def test_extra_sub_questions_are_cut_to_the_configured_cap_and_their_bridges_dropped():
    d = draft([sq("A?"), sq("B?", "mechanism"), sq("C?", "safety"), sq("D?", "epidemiology")],
              [{"from_sub_question": 4, "to_sub_question": 1, "hypothesis": "h"}])
    b = build_plan("r", "Q", d, max_sub_questions=3)
    assert len(b.plan.sub_questions) == 3 and b.plan.effort == "moderate" and b.plan.bridges == []
    assert any("too_many_sub_questions kept 3 of 4" in w for w in b.warnings)
    assert any("bridge_dropped 4->1" in w for w in b.warnings)


@pytest.mark.parametrize("bridge", [
    {"from_sub_question": 0, "to_sub_question": 1, "hypothesis": "h"},    # positions are 1-based
    {"from_sub_question": 1, "to_sub_question": 1, "hypothesis": "h"},    # self-link
    {"from_sub_question": 1, "to_sub_question": 2, "hypothesis": "  "},   # empty
])
def test_bad_bridges_are_dropped_with_a_warning(bridge):
    b = build_plan("r", "Q", draft([sq("A?"), sq("B?", "mechanism")], [bridge]), max_sub_questions=4)
    assert b.plan.bridges == [] and len(b.warnings) == 1


def test_at_most_two_bridges_and_no_repeats():
    br = [{"from_sub_question": f, "to_sub_question": t, "hypothesis": "h"} for f, t in ((1, 2), (1, 2), (2, 1), (1, 2), (2, 1))]
    b = build_plan("r", "Q", draft([sq("A?"), sq("B?", "mechanism")], br), max_sub_questions=4)
    assert [(x.from_sub_question_id, x.to_sub_question_id) for x in b.plan.bridges] == [("sq1", "sq2"), ("sq2", "sq1")]


def test_quality_problems_are_warnings_not_errors():
    b = build_plan("r", "Q", draft([sq("A?", concepts=["x"]), sq("B?", concepts=["a", "a", "b", "c", "d", "e", "f"])]),
                   max_sub_questions=4)
    assert b.plan.sub_questions[1].key_concepts == ["a", "b", "c", "d", "e", "f"]      # deduplicated
    assert sum("key_concepts" in w for w in b.warnings) == 2
    assert any(w.startswith("lead.repeated_facet") for w in b.warnings)


@pytest.mark.parametrize("sqs", [[sq("only")], [sq("A?"), sq("   ")], []])
def test_fewer_than_two_usable_sub_questions_is_an_error(sqs):
    with pytest.raises(InvalidPlan):
        build_plan("r", "Q", draft(sqs), max_sub_questions=4)


def test_prompt_carries_the_question_and_cap_but_not_the_run_id():
    u = load_prompt("lead").render("plan", question="Does X affect Y?", max_sub_questions=3).user
    assert "<question>Does X affect Y?</question>" in u and "at most 3" in u and "run_" not in u
    assert Facet.MECHANISM.value in load_prompt("lead").system
