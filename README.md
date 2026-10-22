# BioLitSynth

Multi-agent biomedical literature synthesis with verified, provenance-tracked claims.
The spec is [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md); rules for coding agents are in [CLAUDE.md](CLAUDE.md).

## Quickstart

    uv venv --python 3.13 .venv && uv pip install -p .venv/bin/python -e '.[dev]'
    .venv/bin/pytest
    .venv/bin/biolitsynth run "Does metformin reduce cancer risk?" --dry

`--dry` walks the whole LangGraph pipeline on stubs (no network, no LLM), forces every bounded loop once, and
writes `runs/<run_id>/` (plan snapshot, claims snapshots, `trace.jsonl`) plus `data/biolitsynth.db`.
