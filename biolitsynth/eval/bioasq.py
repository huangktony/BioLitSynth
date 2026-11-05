"""BioASQ Task B files and the committed subset (plan §11.3b).

The downloaded BioASQ JSON stays in data/eval/bioasq/ (gitignored; its license is unverified). The repo commits only
tests/golden/bioasq_subset.yaml: question ids, the split, the selection rules and a hash of the source file.
`select_subset` applies the selection rules; a person reviews the result before any tuning (and may drop off-topic
questions, never edit labels).
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_PMID = re.compile(r"(\d{1,9})/?$")
TYPES = ("yesno", "summary")


@dataclass(frozen=True)
class BioasqQuestion:
    id: str
    body: str
    type: str
    relevant_pmids: tuple[str, ...]


def load_bioasq(path: str | Path) -> dict[str, BioasqQuestion]:
    """Questions keyed by id. Documents are PubMed URLs such as http://www.ncbi.nlm.nih.gov/pubmed/23727394."""
    data = json.loads(Path(path).read_text())
    out = {}
    for q in data.get("questions", []):
        pmids = []
        for url in q.get("documents", []):
            m = _PMID.search(str(url).strip())
            if m and m.group(1) not in pmids:
                pmids.append(m.group(1))
        out[q["id"]] = BioasqQuestion(q["id"], q["body"].strip(), q.get("type", ""), tuple(pmids))
    return out


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def select_subset(questions: dict[str, BioasqQuestion], *, n: int = 50, dev: int = 30, seed: int = 0,
                  min_relevant: int = 5, types: tuple[str, ...] = TYPES) -> dict:
    eligible = sorted(q.id for q in questions.values() if q.type in types and len(q.relevant_pmids) >= min_relevant)
    if len(eligible) < n:
        raise ValueError(f"only {len(eligible)} questions meet the rules, need {n}")
    picked = random.Random(seed).sample(eligible, n)
    return {"rules": {"types": list(types), "min_relevant_pmids": min_relevant, "n": n, "seed": seed},
            "dev": picked[:dev], "held_back": picked[dev:]}


def write_subset(subset: dict, source: str | Path, out: str | Path) -> None:
    doc = {"source_sha256": file_sha256(source), "reviewed_by": None, **subset}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text("# BioASQ subset (plan §11.3b). Ids only. A person reviews this list before any tuning and\n"
                         "# sets reviewed_by. Dropping an off-topic question is allowed; editing labels is not.\n"
                         + yaml.safe_dump(doc, sort_keys=False))


def load_subset(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text())
