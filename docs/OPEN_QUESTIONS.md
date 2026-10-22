# Open questions

Gaps, inconsistencies and design choices found while building P0, with what was chosen. Items 1-3 are real
contradictions in the plan; the rest are places the plan was silent or loose. Reverse any if the team disagrees.

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
10. **The coverage node owns the round cap.** Appendix A has the Lead keep returning gaps "for reporting only"
    after the last round. The node launches a round only if `rounds < coverage_extra_rounds`; otherwise the gaps
    go to `unresolved_gap_ids` (the Synthesizer turns them into GAP claims, §5.1) and `coverage_rounds` stays put.
    `route_after_coverage` re-checks the cap as a second barrier.
11. **`queries` rows are upserted** so a node re-executed after a crash is idempotent, but reusing an id for a
    *different* sub-question raises. Query ids come from `len(query_ids)`, so removing a query from state in the
    approval UI (P7a) must not shrink that list; mark it removed instead.
12. **Cross-field checks are in the schemas**: `effort` matches the sub-question count (simple 2, moderate 3,
    complex 4), sub-question and bridge ids are unique, bridges point at real sub-questions, and
    `Conflict.leaning_side` is null exactly when `balance == "balanced"`. If the UI's `sub_questions: 2|3|4`
    option is used, `effort` must be set to match.
13. **`papers.authors_json`** added to §6.1's table. Without it a cached paper loses `Paper.authors`, and the
    clusterer's first-author rule (§5.4b) would silently never fire.

## Deviations from the §4 schemas (not in the plan text)
- `EvidenceItem.id` is constrained to `^ev_\d+_\d+$`. Reject-by-default; loosen it if another id format is needed.
- `Paper.journal`, `year`, `doi` default to `None`; `PubMedQuery.filters` defaults to `QueryFilters()`.
- `CoverageGap` is defined (the plan only sketches it in a comment): `suggested_new_concepts` defaults to `[]`.
- `Stage` / `EventType` are exported as named types from `schemas/trace.py`. Definitions were reordered so forward
  references (`Bridge`, `RelevanceScore`, `CoverageGap`) import cleanly.

## Not decided yet
- **`--resume` semantics.** §6.2 describes both snapshot resume (`--from synthesize`) and checkpointer resume
  (`thread_id = run_id`). Not implemented. Suggestion: CLI `--resume` = snapshots only; the checkpointer is crash
  recovery only. Decide before P5.
- **LLM-facing `*Draft` schemas: required, decide the shape before the first P2 prompt.** Invariant 1 says
  code owns ids, offsets, strength, statuses and weights, so the §4 models cannot be the model's output schema as
  written. Fields the LLM must NOT produce: `EvidenceItem` id, quote_start/end, strength, study_id, canonical_*,
  extractor_model, prompt_version; `Verdict` status, attempt, critic_model; `Conflict` balance, leaning_side,
  sides[].weight; `ResearchPlan` run_id. Code builds the full model from the draft. Providers also reject some
  constructs (`tuple` in `refinement_history`, regex `pattern`, `model_validator`), which drafts avoid too.
- **`FinalReport`** (§5.12) is described but not defined in §4. Define in P5.
- **Model IDs.** `config.yaml` holds `TODO-verify-*` placeholders until someone checks AI Studio / build.nvidia.com.

## Deferred, with a reason
- **Trace start events (P7a).** Spans are written once, when they end, with `ts` = end time. The live pipeline
  view needs "stage started"; either read `graph.astream` updates, or insert a `running` row at span open and
  update it at close (also leaves a trace of spans that die mid-run). Needs a `running` status on `TraceEvent`.
- **`approve_queries` resume value (P7a).** The node ignores what `interrupt()` returns and re-logs its event on
  resume. The researcher's edits arrive in that value, so apply them there when the API is built.
- **Config and paths are relative to the working directory.** Fine from the repo root (README says so).

## Before starting P1 (nothing below exists yet)
- `pubmed/` package; `httpx` and `lxml` (or Biopython; pick one) in `pyproject.toml`.
- A shared async token-bucket rate limiter; an `ncbi:` block in `config.yaml` (tool, email; `NCBI_API_KEY` from env).
- Caches: `data/cache/pubmed/<pmid>.xml` and the esearch cache (§6.3, 7-day TTL). The latter has **no table in
  §6.1**; add one.
- `tests/fixtures/pubmed/*.xml` must be recorded from real efetch calls by a person (CLAUDE.md fixture rule),
  including a retracted paper, a structured abstract with inline markup, a missing abstract, and a `MedlineDate`.
- Test "re-run makes 0 network calls" offline with an `httpx.MockTransport` that counts requests; keep the live
  25-paper fetch as a `-m live` test.
- P0 stubs have no real `await` points, so Send branches run nearly serially. Concurrency bugs (interleaved SQLite
  writes, span parenting under real I/O) are not exercised at graph level yet; P1's fetch fan-out is the first test.
