# BioLitSynth: rules for coding agents

`docs/IMPLEMENTATION_PLAN.md` is the spec. Read the section for the phase you are in (§12) before writing code.
Current status: **P0 skeleton done** (graph runs on stubs). Next: P1 retrieval. Read `docs/OPEN_QUESTIONS.md` first.

## Scope guard
- Do not add agents, data sources, frameworks, vector stores, or UI pages the plan doesn't list.
- LangGraph is the only orchestration framework. No `langchain*` imports (a test enforces this).
- Don't start a phase before the previous one's acceptance criteria pass.
- Ambiguous requirement? Append it to `docs/OPEN_QUESTIONS.md`, pick the simplest option consistent with the plan.

## Invariants
1. LLMs never produce PMIDs, quotes, offsets, strength grades, or verdict statuses that code can produce or check.
2. Every LLM output is a Pydantic model from `biolitsynth/schemas/`. No regex parsing of model text.
3. Every LLM or network call goes through the cached, rate-limited client and emits a trace span.
4. Prompts are versioned files in `prompts/`. Bump the version when you change one.
5. Model IDs live in `config.yaml` only. Never invent a model ID; the `TODO-verify-*` placeholders are intentional.

## Graph rules (biolitsynth/graph/)
- Nodes are thin: open a span, call a stage function, write to SQLite, return IDs and counters.
- A key written by parallel branches (`Send`) MUST use the `merge_unique` reducer. Every other key has one
  writer and is overwritten. Never make a counter or a "current failures" list append-only.
- Routers are pure functions of state in `routers.py`. A conditional edge must always lead somewhere:
  fan-outs return a node name when there is nothing to fan out over (an empty list ends the run silently).
- Conditional edges point at nodes, never at other router functions.
- Loops are bounded by counters in state AND `recursion_limit`.

## Tests
- `.venv/bin/pytest` is offline and uses `FakeClient` + recorded fixtures. `-m live` / `-m slow` are opt-in.
- **Fixtures are written or approved by a person, or recorded from a live run.** Never let the agent that wrote
  the code under test also write the expected answer, or the test proves nothing.
- Acceptance criteria that need live APIs (P2-P4) are checked by a person at the weekly checkpoint.

## Commands
    uv venv --python 3.13 .venv && uv pip install -p .venv/bin/python -e '.[dev]'
    .venv/bin/pytest
    .venv/bin/biolitsynth run "your question" --dry
