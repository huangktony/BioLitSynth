"""Versioned prompt loading (plan §5.0)."""
import jinja2
import pytest

from biolitsynth.llm import PromptError, load_prompt
from biolitsynth.schemas import Facet, SubQuestion

BODY = "<!-- system -->\n# Role\nsys {n}\n<!-- user:go -->\nHello {{{{ name }}}}\n"


def write(d, name, n, body=None):
    (d / f"{name}.v{n}.md").write_text(body if body is not None else BODY.format(n=n))


def test_highest_version_wins_and_carries_version_and_hash(tmp_path):
    write(tmp_path, "demo", 1)
    write(tmp_path, "demo", 2)
    write(tmp_path, "demox", 9)                                  # another prompt's file is not picked up
    p = load_prompt("demo", directory=tmp_path)
    assert p.version == "demo.v2" and p.system == "# Role\nsys 2"
    r = p.render("go", name="Ada")
    assert (r.user, r.version, r.hash) == ("Hello Ada", "demo.v2", p.hash) and len(p.hash) == 64


def test_any_edit_changes_the_hash(tmp_path):
    write(tmp_path, "demo", 1)
    h1 = load_prompt("demo", directory=tmp_path).hash
    write(tmp_path, "demo", 1, BODY.format(n=1) + " ")
    assert load_prompt("demo", directory=tmp_path).hash != h1


@pytest.mark.parametrize("body, msg", [
    ("intro\n<!-- system -->\nx\n<!-- user:go -->\ny", "before the first section"),
    ("<!-- user:go -->\ny", "missing <!-- system -->"),
    ("<!-- system -->\nx", "at least one <!-- user:"),
])
def test_malformed_files_are_refused(tmp_path, body, msg):
    write(tmp_path, "demo", 1, body)
    with pytest.raises(PromptError, match=msg):
        load_prompt("demo", directory=tmp_path)


def test_missing_file_unknown_section_and_missing_variable_fail_loudly(tmp_path):
    with pytest.raises(PromptError, match="no prompt file"):
        load_prompt("nope", directory=tmp_path)
    write(tmp_path, "demo", 1)
    p = load_prompt("demo", directory=tmp_path)
    with pytest.raises(PromptError, match="no user:other"):
        p.render("other")
    with pytest.raises(jinja2.UndefinedError):
        p.render("go")


def test_query_builder_prompt_has_both_templates_and_renders_concepts():
    p = load_prompt("query_builder")
    assert set(p.templates) == {"draft", "refine"}
    for heading in ("# Role", "# Objective", "# Inputs", "# Output schema", "# Rules", "# Examples",
                    "# What NOT to do"):
        assert heading in p.system                                # plan §5.0 template
    sq = SubQuestion(id="sq2", text="Does X lower Y?", facet=Facet.SAFETY, rationale="r", key_concepts=["X", "Y"])
    user = p.render("draft", sub_question=sq, min_hits=20, max_hits=2000).user
    assert "(sq2): Does X lower Y?" in user and "Key concepts: X; Y" in user and "safety" in user
    empty = sq.model_copy(update={"key_concepts": []})
    assert "none given" in p.render("draft", sub_question=empty, min_hits=20, max_hits=2000).user
