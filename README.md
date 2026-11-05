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

To build PubMed queries with the LLM Query Builder (P2), set a real Gemini model id in `config.yaml`
(`models.query`), export `GEMINI_API_KEY`, and give 2 to 4 sub-questions by hand (the Lead's plan call isn't built yet):

    .venv/bin/biolitsynth run "Does metformin reduce colorectal cancer risk?" \
      --sub-question "metformin use and colorectal cancer incidence | metformin; colorectal neoplasms" \
      --sub-question "randomized trials of metformin in colorectal cancer"

It prints each query's attempts and hit counts, then fetches and dedups the papers and stops. A rerun of the same
command makes 0 LLM calls and 0 NCBI calls.

`--dry` walks the whole LangGraph pipeline on stubs (no network, no LLM), forces every bounded loop once, and
writes `runs/<run_id>/` (plan snapshot, claims snapshots, `trace.jsonl`) plus `data/biolitsynth.db`.
