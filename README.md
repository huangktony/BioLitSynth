# BioLitSynth

Multi-agent biomedical literature synthesis with verified, provenance-tracked claims.
The spec is [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md); rules for coding agents are in [CLAUDE.md](CLAUDE.md).

## Quickstart

    uv venv --python 3.13 .venv && uv pip install -p .venv/bin/python -e '.[dev]'
    .venv/bin/pytest
    .venv/bin/biolitsynth run "Does metformin reduce cancer risk?" --dry

To try retrieval on its own (live NCBI, 3 requests per second without a key; set `NCBI_API_KEY` for 10):

    .venv/bin/biolitsynth fetch "metformin AND colorectal neoplasms[mh] AND humans[mh]"

Run it twice: the second run reports `network calls 0`.

Live runs (P2) need a Gemini key and NCBI key in `.env` (gitignored; see the variable names in `config.yaml`):

    GEMINI_API_KEY=...
    NCBI_API_KEY=...

Then the Lead plans the question, the Query Builder writes and refines one PubMed query per sub-question, and the
papers are fetched and deduplicated (later stages aren't built yet, so the run stops there):

    .venv/bin/biolitsynth run "Does metformin reduce cancer incidence in patients with type 2 diabetes, and through what mechanisms?"

Add `--sub-question "text | concept; concept"` (2 to 4 times) to skip the Lead. A rerun of the same command makes
0 LLM calls and 0 NCBI calls.

Retrieval eval on BioASQ (plan §11.3b; download the Task B training set yourself into `data/eval/bioasq/`):

    .venv/bin/biolitsynth eval select --bioasq data/eval/bioasq/training.json      # a person reviews the ids
    .venv/bin/biolitsynth eval retrieval --bioasq data/eval/bioasq/training.json --split dev

`--dry` walks the whole LangGraph pipeline on stubs (no network, no LLM), forces every bounded loop once, and
writes `runs/<run_id>/` (plan snapshot, claims snapshots, `trace.jsonl`) plus `data/biolitsynth.db`.
