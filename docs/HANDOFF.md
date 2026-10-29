# Handoff: state of the project (Thu Oct 29, 2026)

Written for whoever picks this up next. Read this first, then `docs/OPEN_QUESTIONS.md`, then `docs/ROADMAP.md`.

## Where things stand

- **P0 (skeleton) is done and pushed.** `origin/main` is at `05043a5`.
- `docs/ROADMAP.md` (weekly checkpoints) is written but **not yet committed or pushed**.
- 97 tests pass offline in about 1 second. `biolitsynth run "..." --dry` walks the whole graph on stubs, forcing one
  query refinement, one gap round and one Synthesizer retry (expected output: `gap rounds=1 synth attempts=2
  queries=4 evidence=16`, status `assembled`).
- **Nothing real runs yet.** No PubMed calls, no LLM calls. Every pipeline node is a stub in
  `biolitsynth/graph/dry.py`. P0 proves the wiring, not the science.

## Set up and check in 2 minutes

    uv venv --python 3.13 .venv && uv pip install -p .venv/bin/python -e '.[dev]'
    .venv/bin/pytest
    .venv/bin/biolitsynth run "Does metformin reduce cancer risk?" --dry

Output goes to `runs/<run_id>/` (plan and claims snapshots, `trace.jsonl`) and `data/` (SQLite). Both are gitignored.

## Read these, in this order

1. `docs/OPEN_QUESTIONS.md`: 13 places where the plan was inconsistent or silent, what was chosen, and what is still open.
2. `docs/ROADMAP.md`: who builds what each week, the two gates, the cut list.
3. `CLAUDE.md`: rules for coding agents (scope guard, graph rules, fixture rule).
4. `docs/IMPLEMENTATION_PLAN.md` (v1.4): the spec. Section 12 is the phase table.

## Decisions waiting on a person

| Decision | Why it matters | By |
|---|---|---|
| Real model IDs (8 `TODO-verify-*` entries in `config.yaml`) | Nobody has verified any model name; agents will invent them | W0 (Nov 1) |
| Keys: Gemini, NVIDIA NIM, NCBI | NIM needs phone verification; blocks P2 | W0 |
| Shape of the LLM-facing `*Draft` schemas | The plan's models contain fields code must own (ids, offsets, strength, statuses). Required before the first prompt | W0, built W1 |
| Meaning of `--resume` | Plan describes snapshot resume and checkpointer resume; only one should exist | W0 |
| Add baselines (approve-all, flag-all, PMID-exists-only) to the eval? | Not in the plan, so it needs a deliberate yes | W0 |
| `EvidenceItem.id` pattern `^ev_\d+_\d+$` | Added in code, not in the plan; rejects any other id format | W0 |
| Demo date (roadmap assumes Fri Dec 11) | Every week boundary depends on it | now |
| Golden-question anchor PMIDs | Must be picked by a person; they are test answers | W0 |

## What was decided in P0 (the non-obvious ones)

- The coverage node, not the stub or the router, owns the "one extra round" cap; leftover gaps go to
  `unresolved_gap_ids` so they can become GAP claims. Tested, and the test fails if the cap is removed.
- Claim ids are `cl4`, not `cl_04` (the schema pattern wins over the plan's prose).
- A `route` node sits between the checks and the router, because LangGraph edges can only point at nodes.
- Run-scoped tables use `(run_id, id)` keys; the plan's `evidence(id PK)` would collide across runs.
- Parallel (`Send`) branches may only write keys that use the `merge_unique` reducer. This is the easiest thing for
  an agent to break; tests catch it.

## Known weak spots (do not assume these are fine)

- **Concurrency is untested at graph level.** The stubs have no real `await`, so branches run nearly in sequence.
  P1's parallel fetch is the first real test of SQLite writes and span parenting under load.
- **Trace spans are written once, at the end**, so a live "stage started" view and crash forensics both need a
  tracer change (deferred to P7a, noted in OPEN_QUESTIONS).
- **`approve_queries` ignores the value passed to `interrupt()`** and logs twice on resume. Fix when the API is built.
- **Placeholder model IDs pass config validation**; a live run would only fail at the first API call.
- **The web app as specified in the plan does not fit the calendar.** See the roadmap; decide at Gate 1 (Nov 20).

## Rules that bite

- Fixtures under `tests/fixtures/` are recorded by a person or from a live run. The agent that writes the code never
  writes its expected answers.
- Anything needing live APIs is checked by a person at the Friday sync, not by an agent.
- Do not add agents, data sources, frameworks or UI pages the plan does not list. Put questions in `OPEN_QUESTIONS.md`.
- Ask before `git push`. Never put keys in the repo or in chat; use variable names.

## Next action

P1 retrieval (see `docs/ROADMAP.md`, W1). Before writing code, B records the real efetch XML fixtures with `curl`
(retracted paper, inline markup, structured abstract, no abstract, `MedlineDate`) so the parser is tested against
data it did not write.
