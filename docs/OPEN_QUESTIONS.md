# Open questions

Ambiguities found while building P0, with the choice made. Reverse any of these in the plan if the team disagrees.

## Decided in code (plan text and plan code disagree, or are silent)
1. **Claim ID format.** Prose uses `cl_04` / `[cl_XX]`; the schema pattern is `^cl\d+$` (no underscore).
   Chose the schema: `cl4`. The Synthesizer prompt and the sentence-marker parser (P5) must use `[cl4]`.
2. **`route_after_checks` pointed an edge at a function** (`hard_fail: route_after_critic`). Conditional edges
   can only name nodes, and the plan's version skipped the critic for *all* claims if one hard-failed.
   Added a `route` node (stage `route`): `det_checks → critique | route`, `critique → route`,
   `route → synthesize | assemble`. Claims that hard-failed skip the critic; the rest are still critiqued.
3. **Retry counting.** `synth_attempt` is the index of the latest synthesis (first = 0). A retry happens while
   `synth_attempt < 2`, so at most three syntheses: the first plus two retries.
4. **Run-scoped primary keys.** The plan's `evidence(id PK, run_id…)` and `claims(id, run_id, attempt)` collide
   across runs (`ev_<pmid>_<n>` repeats whenever two runs fetch the same paper). Run-scoped tables use
   `(run_id, id[, attempt])` keys. Only `papers`, `llm_cache` and `trace_events.span_id` are globally keyed.
5. **When query refinement triggers.** §5.2 says refine at 0 hits or >5,000, but also "target band 20-2,000", and
   the P2 acceptance criterion requires the band. Chose: refine whenever the count is outside the band, up to the cap.
6. **Checkpointer.** Nodes are async, so the checkpointer is `AsyncSqliteSaver`, not `SqliteSaver` (§8.1 rule 6).
7. **Quote re-extraction (≤1).** The §3 diagram draws a loop QC → EX. In the graph it is an internal retry
   inside `quote_check` (P3), not a cycle. Only the four loops in §1.3 are graph cycles.
8. **Trace shape.** `Stage` has no "run"/"dispatch" value, so top-level node spans are trace roots and
   Send fan-outs hang their spans under a `handoff` event emitted by the dispatching node.
9. **Python ≥3.11**, dependencies pinned exactly in `pyproject.toml` (LangGraph's API moves between minors).

## Not decided yet
- **`--resume` semantics.** §6.2 describes both snapshot resume (`--from synthesize`) and checkpointer resume
  (`thread_id = run_id`). Not implemented. Suggestion: CLI `--resume` = snapshots only; the checkpointer is crash
  recovery only. Decide before P5.
- **Provider JSON-schema limits.** Some providers reject `tuple` (`refinement_history`), regex `pattern`, and
  `model_validator`. Decide in P2 whether LLM-facing `*Draft` models are needed, with code promoting them to the
  full models (also keeps ids, offsets and strength out of the model's hands).
- **`FinalReport`** (§5.12) is described but not defined in §4. Define in P5.
- **Model IDs.** `config.yaml` holds `TODO-verify-*` placeholders until someone checks AI Studio / build.nvidia.com.
