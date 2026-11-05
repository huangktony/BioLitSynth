"""Versioned prompt files (plan §5.0): prompts/<agent>.v<N>.md.

File layout. Sections are introduced by HTML-comment markers so the system prompt can use Markdown headings freely:

    <!-- system -->
    # Role ...                         fixed across runs
    <!-- user:draft -->
    Sub-question: {{ sub_question.text }}   Jinja2 template, per run data only
    <!-- user:refine -->
    ...

The highest N present is the live version unless config.yaml pins one (`prompt_versions`). Changing a prompt means
adding the next version, so the version string
on every cached output and trace span keeps pointing at the text that produced it. The hash is of the whole file.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import jinja2

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
_MARKER = re.compile(r"^<!--\s*(system|user:[a-z_]+)\s*-->\s*$", re.M)
_FILE = re.compile(r"^(?P<name>[a-z_]+)\.v(?P<n>\d+)\.md$")
_ENV = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False, keep_trailing_newline=False,
                          trim_blocks=True, lstrip_blocks=True)


class PromptError(ValueError):
    pass


@dataclass(frozen=True)
class RenderedPrompt:
    system: str
    user: str
    version: str        # e.g. "query_builder.v1"
    hash: str           # sha256 of the prompt file


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    hash: str
    system: str
    templates: dict[str, str]

    def render(self, kind: str, **data) -> RenderedPrompt:
        if kind not in self.templates:
            raise PromptError(f"{self.version} has no user:{kind} section (has {sorted(self.templates)})")
        user = _ENV.from_string(self.templates[kind]).render(**data).strip()
        return RenderedPrompt(self.system, user, self.version, self.hash)


def load_prompt(name: str, *, version: int | None = None, directory: str | Path | None = None) -> Prompt:
    """`version=None` loads the highest version present; an int loads exactly that one."""
    d = Path(directory) if directory else PROMPTS_DIR
    found = {int(m["n"]): p for p in d.glob(f"{name}.v*.md") if (m := _FILE.match(p.name)) and m["name"] == name}
    if not found:
        raise PromptError(f"no prompt file {name}.v<N>.md in {d}")
    if version is not None and version not in found:
        raise PromptError(f"no prompt file {name}.v{version}.md in {d} (have {sorted(found)})")
    n = max(found) if version is None else version
    raw = found[n].read_text()
    parts = _MARKER.split(raw)
    if parts[0].strip():
        raise PromptError(f"{found[n].name}: text before the first section marker")
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}
    if "system" not in sections or not sections["system"]:
        raise PromptError(f"{found[n].name}: missing <!-- system --> section")
    templates = {k.removeprefix("user:"): v for k, v in sections.items() if k.startswith("user:")}
    if not templates:
        raise PromptError(f"{found[n].name}: needs at least one <!-- user:<kind> --> section")
    return Prompt(name=name, version=f"{name}.v{n}", hash=hashlib.sha256(raw.encode()).hexdigest(),
                  system=sections["system"], templates=templates)
