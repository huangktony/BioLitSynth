"""Load KEY=value lines from a local .env file into os.environ (no dependency needed for this).

Variables already set in the environment win, so `export X=...` overrides the file. Values are never logged.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import MutableMapping


def load_env_file(path: str | os.PathLike = ".env", *, environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Returns the names it set. Blank lines and # comments are skipped; a value may be wrapped in quotes."""
    env = os.environ if environ is None else environ
    if env.get("BIOLITSYNTH_NO_DOTENV"):          # set by the test suite: tests never see real keys
        return []
    p = Path(path)
    if not p.is_file():
        return []
    set_names = []
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.removeprefix("export ").partition("=")
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name and name not in env:
            env[name] = value
            set_names.append(name)
    return set_names
