# Recorded LLM replies

Exported from the LLM cache after live runs (`llm_cache` table), not written by hand.

| File | Run | Model | Prompt |
|---|---|---|---|
| `query_builder_live_20261105.json` | Live check 2026-11-05: metformin/CRC (2 sub-questions, both in band on the first draft) and a broad "effects of metformin" sub-question (narrowed twice, stopped at the cap at 2,183 hits) | `gemini-3.8-flash` | `query_builder.v1` |
| `lead_live_20261105.json` | Live check 2026-11-05: golden Q1 (metformin, cancer incidence in T2D, mechanisms) → 3 sub-questions, facets clinical_outcome / mechanism / epidemiology, 1 bridge | `gemini-3.8-flash` | `lead.v1` |

Re-record after any prompt version bump: run the same commands, then
`sqlite3 -json data/biolitsynth.db "SELECT model, prompt_version, response_json, created_at FROM llm_cache WHERE prompt_version='<version>'"`.
