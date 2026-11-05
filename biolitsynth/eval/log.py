"""Committed eval log: one JSON line per eval in docs/eval_log.jsonl (plan §11.5 rule 4).

A record holds settings, hashes, summary numbers and per-question recall by question id. It never holds question
text, relevant PMIDs or query strings: the BioASQ source file's license is unverified, so only ids are committed.
Those details stay in the local runs/<eval_id>/retrieval.json.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..agents import query_builder as qbuild
from ..llm import load_prompt

DEFAULT_LOG = Path("docs/eval_log.jsonl")


def config_hash(config_json: str) -> str:
    canon = json.dumps(json.loads(config_json), sort_keys=True)
    return hashlib.sha256(canon.encode()).hexdigest()[:12]


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _per_question(q: dict) -> dict:
    m = list(q["misses"].values())
    return {"recall_fetch": q["recall_fetch"], "recall_fetch_draft": q["recall_fetch_draft"],
            "refinements": q["refinements"], "in_band": q["in_band"],
            "misses_beyond_cap": m.count("beyond_cap"), "misses_not_matched": m.count("not_matched")}


def _record(*, eval_id, date, split, model, prompt_version, retmax, summary, questions: list[dict],
            config_json: str, git_commit, git_dirty, **extra) -> dict:
    return {"eval_id": eval_id, "date": date, "split": split, "n_questions": len(questions),
            "question_ids": [q["id"] for q in questions], "model": model, "prompt_version": prompt_version,
            "prompt_hash": load_prompt(qbuild.PROMPT).hash, "config_hash": config_hash(config_json),
            "retmax": retmax, "git_commit": git_commit, "git_dirty": git_dirty, "summary": summary,
            "per_question": {q["id"]: _per_question(q) for q in questions}, **extra}


def record_from_report(report, settings, *, config_json: str) -> dict:
    """`settings` is accepted for symmetry with the callers; everything stored comes from the report and config_json."""
    status = _git("status", "--porcelain", "--untracked-files=no")
    questions = [{"id": q.id, "misses": q.misses, "recall_fetch": q.recall_fetch,
                  "recall_fetch_draft": q.recall_fetch_draft, "refinements": q.refinements, "in_band": q.in_band}
                 for q in report.questions]
    return _record(eval_id=report.eval_id, date=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   split=report.split, model=report.model, prompt_version=report.prompt_version,
                   retmax=report.retmax, summary=report.summary(), questions=questions, config_json=config_json,
                   git_commit=_git("rev-parse", "--short", "HEAD"),
                   git_dirty=None if status is None else bool(status))


def record_from_retrieval_json(path: str | Path, config_json: str) -> dict:
    """Backfill from a written retrieval.json. The date comes from the eval id; git fields are unknown; the prompt
    hash is that of the prompt file as it is now."""
    d = json.loads(Path(path).read_text())
    m = re.match(r"eval_(\d{8})_(\d{6})_", d["eval_id"])
    date = (datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            .isoformat(timespec="seconds") if m else None)
    return _record(eval_id=d["eval_id"], date=date, split=d["split"], model=d["model"],
                   prompt_version=d["prompt_version"], retmax=d["retmax"], summary=d["summary"],
                   questions=d["questions"], config_json=config_json, git_commit=None, git_dirty=None,
                   backfilled=True)


def read_log(path: str | Path = DEFAULT_LOG) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def append_log(record: dict, path: str | Path = DEFAULT_LOG) -> None:
    p = Path(path)
    if any(r["eval_id"] == record["eval_id"] for r in read_log(p)):
        raise ValueError(f"eval {record['eval_id']} is already in {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(record, sort_keys=False) + "\n")


def run_config_json(settings, eval_id: str) -> str:
    """The config the eval's runs row stored when it started."""
    from ..store import Database
    db = Database(settings.paths.data_dir / "biolitsynth.db")
    try:
        return db.get_run(eval_id)["config_json"]
    finally:
        db.close()


def backfill(settings, path: str | Path = DEFAULT_LOG) -> list[str]:
    """Append every completed eval in the store that has a retrieval.json and is not logged yet."""
    from ..store import Database
    db = Database(settings.paths.data_dir / "biolitsynth.db")
    try:
        rows = db.query("SELECT run_id, config_json FROM runs WHERE run_id LIKE 'eval\\_%' ESCAPE '\\' "
                        "AND status = 'completed_eval' ORDER BY created_at, run_id")
    finally:
        db.close()
    have = {r["eval_id"] for r in read_log(path)}
    added = []
    for r in rows:
        rj = settings.paths.runs_dir / r["run_id"] / "retrieval.json"
        if r["run_id"] in have or not rj.exists():
            continue
        append_log(record_from_retrieval_json(rj, r["config_json"]), path)
        added.append(r["run_id"])
    return added
