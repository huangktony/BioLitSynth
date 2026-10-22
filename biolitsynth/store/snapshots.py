"""Stage snapshots (plan §6.2): runs/<run_id>/<NN>_<stage>.json. For people, diffs and fixtures."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def snapshot_path(run_dir: str | Path, index: int, stage: str, attempt: int | None = None) -> Path:
    suffix = f"_a{attempt}" if attempt is not None else ""
    return Path(run_dir) / f"{index:02d}_{stage}{suffix}.json"


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, (list, tuple)):
        return [_jsonable(o) for o in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    return obj


def write_snapshot(run_dir: str | Path, index: int, stage: str, payload: Any,
                   attempt: int | None = None) -> Path:
    p = snapshot_path(run_dir, index, stage, attempt)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_jsonable(payload), indent=2, sort_keys=True) + "\n")
    return p


def read_snapshot(run_dir: str | Path, index: int, stage: str, attempt: int | None = None) -> Any:
    return json.loads(snapshot_path(run_dir, index, stage, attempt).read_text())
