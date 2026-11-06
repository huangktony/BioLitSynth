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
14. **PubMed's retraction-notice type is `Retraction Notice`, not `Retraction of Publication`** (§5.4). Verified
    against live efetch output on 2026-11-02; a search for the plan's name returns 0 records. Dedup drops both names,
    plus `Published Erratum` and `Comment`. `Retracted Publication` (the paper itself) matches the plan.
15. **No `usehistory=y`.** §5.3 says esearch with `usehistory=y`, then efetch. We pass explicit PMID lists (at most 200)
    because the cache is per PMID, so a WebEnv handle adds nothing.
16. **Registry ids come from two `DataBankName`s only** (ClinicalTrials.gov, ISRCTN) plus the `NCT\d{8}|ISRCTN\d{8}`
    regex over the abstract. Other databanks (GenBank, PDB, ...) are not study identifiers and are ignored.
17. **Abstract text format.** Structured abstracts become `LABEL: text` lines joined by `\n` (no trailing newline),
    labels exactly as PubMed spells them (`CLINICAL TRIAL` has a space). Plain abstracts are stored as written.
    Nothing is normalised at parse time; the quote validator (P1, other half) normalises.
18. **The seed `StudyDesign` from publication types (§5.4) is not built.** `Paper` has no field for it and nothing
    consumes it before the extractor, so it moves to P3.
19. **A `biolitsynth fetch "<query>"` command was added** as the P1 demo (the plan lists only `run`). Delete it if
    you would rather keep the CLI to `run`.
20. **Rate limiter spaces requests evenly (no burst)**, 3 per second without an NCBI key and 10 with one.
21. **About 1 in 5 search hits has no abstract** (5 of 25 in the recorded sample) and is skipped, so
    `retmax_candidates=100` yields roughly 80 usable papers before reranking. Two queries that run at the same time
    and share a PMID can each download it; the cache makes the next run free, so this is left alone.

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
- **Plan v1.5 needs Tony's review (added 2026-11-05).** It adds the §11.5 decision register (D1–D14: every tunable
  choice decided by a pre-registered experiment), BioASQ as evaluation-only data (§11.3b), and a conditional hybrid
  dense-retrieval branch that is built only if D6 triggers. To confirm: the proposed thresholds in §11.5, the 30/20
  BioASQ dev/held-back split, and that BioASQ counts as eval data, not a new source.
- **BioASQ access and license.** Registration and the data license are unverified. Check both before P2; until then,
  commit only question IDs (`tests/golden/bioasq_subset.yaml`), never the questions or labels.
- **A person must pick the BioASQ subset** (selection rules in §11.3b) before any tuning, so the split cannot be
  shaped by results.

## Deferred, with a reason
- **Trace start events (P7a).** Spans are written once, when they end, with `ts` = end time. The live pipeline
  view needs "stage started"; either read `graph.astream` updates, or insert a `running` row at span open and
  update it at close (also leaves a trace of spans that die mid-run). Needs a `running` status on `TraceEvent`.
- **`approve_queries` resume value (P7a).** The node ignores what `interrupt()` returns and re-logs its event on
  resume. The researcher's edits arrive in that value, so apply them there when the API is built.
- **Config and paths are relative to the working directory.** Fine from the repo root (README says so).

## P1 status (retrieval)
Done: `pubmed/` (rate limiter, parser, client, dedup), `httpx` + `lxml` pinned, `ncbi:` config block (secrets by
env var name: `NCBI_API_KEY`, `NCBI_EMAIL`), per-PMID XML cache, `esearch_cache` table, recorded fixtures in
`tests/fixtures/pubmed/` (see its README), real `fetch` and `dedup` nodes behind `dry=False`.

Still open:
- Fixtures were recorded by a coding agent; the other teammate should review them (checkbox in the fixtures README).
- The first live concurrency test is the 3-query graph run; it uses a mock NCBI, so real parallel fetches against
  NCBI itself have only been exercised by the single-query `fetch` demo.
- B's half of W1 (draft schemas, quote-span validator, the first deterministic checks) is not started.

## P2 status
Started 2026-11-05 with Tony's go-ahead. Code for P2 is complete; acceptance still needs the BioASQ run (a person).

Done:
- LLM clients: `llm/gemini.py` (google-genai) and `llm/openai_compat.py` (NIM / Groq / OpenRouter, plain httpx),
  sharing `llm/retry.py` (429/5xx backoff, one repair retry). `llm/stage.py` (`StageLLM`: span → cache → budget →
  rate limit → client). `llm/prompts.py` (versioned prompt files). Local `.env` loading (`biolitsynth/env.py`).
- Lead plan call: `prompts/lead.v1.md`, `PlanDraft`, `agents/lead.py`. A live run with no `--sub-question` plans
  with the Lead. Live on golden Q1: 3 sub-questions, facets clinical_outcome / mechanism / epidemiology, 1 bridge,
  all 3 queries in band on the first draft (127, 469, 417 hits), 271 papers after dedup.
- Query Builder: `prompts/query_builder.v1.md`, `QueryDraft`, `agents/query_builder.py`, real subgraph.
- BioASQ retrieval eval (§11.3b): `biolitsynth eval select` (applies the selection rules, writes ids only) and
  `biolitsynth eval retrieval` (recall@fetch, the first draft's recall for D2, miss buckets).
- W1 verifier half: `checks/quote_span.py` (§5.5) and `checks/deterministic.py` with the seven schema-only §5.9
  checks; the four W1 injections from §11.4 are caught. Not wired into the graph yet: claims are stubs until P5.
- Recorded live replies in `tests/fixtures/llm/` (Lead and Query Builder).

Decided in code (the plan is silent or loose):
22. **Sub-questions are given by hand until the Lead exists.** `--sub-question "text | concept; concept"`, 2-4 of
    them, facet `other`. The plan node uses them in non-dry runs and raises if there are none.
23. **Live runs stop after `dedup`** (LangGraph `interrupt_after`), because rerank and everything later are still
    stubs. Status `stopped_after_dedup`. No graph edges changed.
24. **Which attempt becomes the query:** the first in-band attempt; otherwise the non-zero attempt closest to the band
    by ratio (10 hits vs a floor of 20 and 4,000 vs a cap of 2,000 are equally far, earlier wins ties); if every
    attempt had 0 hits, the last. §5.2 only says "refine ≤2"; it never says what to keep when all attempts miss.
25. **A query PubMed rejects as malformed counts as 0 hits**, so the loop broadens it. Network and HTTP failures are
    still errors (`QueryRejected` is a separate exception from `PubMedError`).
26. **Code tells the model the direction** (broaden / narrow) and shows every earlier attempt with its count. The
    model only writes the query string and a one-sentence rationale (`QueryDraft`). Filters live inside the query
    string, so `QueryFilters` is unused and `queries.filters_json` is `{}`.
27. **Prompt file format:** `<!-- system -->` and `<!-- user:<kind> -->` markers, Jinja2 user templates with
    StrictUndefined. The highest `v<N>` file is live; change a prompt by adding the next version.
28. **LLM cache key** = sha256(model, prompt version, system prompt, rendered user message, schema name and JSON
    schema). Budget (`budget.max_llm_calls_per_run`) counts uncached calls only. Exceeding it fails the run; the
    plan's "assemble a partial report" (§8.2) waits for the assembler (P5).
29. **New config block `providers.gemini`** (`api_key_env: GEMINI_API_KEY`, `rpm: 10`, `max_retries: 4`). The rpm is a
    guess; set it from the AI Studio limits page. A placeholder model id or a missing key fails before any run
    state is written.
30. **New dependencies:** `google-genai==2.31.0` (released 2026-10-14, the newest at least two weeks old) and
    `jinja2==3.1.6`.
31. **Each refinement costs one extra esearch** (count-only, `retmax=0`), cached 7 days like every esearch.

32. **`limits.sub_questions` is the Lead's maximum** (default 3, cap 4); dry runs still use it as the exact count.
    The Lead may return 2 to that number; extras are cut and logged.
33. **The Lead's draft has no ids and no effort.** Code numbers sub-questions `sq1…`, sets effort from the count
    (2/3/4 → simple/moderate/complex), refers to bridges by 1-based position and drops bad ones. Repeated facets and
    key-concept counts outside 2-5 are trace warnings (`lead.*`), not errors: they are what D1 measures.
34. **The Lead's user message omits the run id** (Appendix A includes it), so the same question hits the LLM cache.
35. **`.env` is read by a 20-line loader**, not python-dotenv; exported variables win. The test suite sets
    `BIOLITSYNTH_NO_DOTENV=1` and clears key variables. Before that guard existed, one test made a real live run on
    the question "q?" (one Lead and a few Query Builder/NCBI calls); its cached reply was not kept as a fixture.
36. **OpenAI-compatible structured output:** `response_format: json_schema` by default, `structured: json_object`
    (schema in the system prompt) for servers without it. Not checked live: there is no NIM key yet.
37. **Retrieval eval details:** recall@fetch is measured on esearch candidates before efetch, so papers without an
    abstract still count (slightly optimistic). Missed PMIDs are tested with `(<query>) AND (<pmid>[pmid] OR …)`
    probes: `beyond_cap` or `not_matched`. Splitting `not_matched` into buckets c/d needs a broad query (D6, P3).
    Default budget for one eval: 200 LLM calls.
38. **Quote validator:** fuzzy matching only for quotes of 20+ characters; snapped spans are trimmed of edge
    whitespace; soft hyphens are dropped during normalization; the stored quote is always `abstract[start:end]`.
39. **Check results for the whole report use `claim_id="*"`.** `pmid_in_run` covers both the evidence behind each
    claim and every citation in the sentences.

Still open:
- **BioASQ, by a person:** register, download the Task B training set to `data/eval/bioasq/`, check its license,
  run `biolitsynth eval select --bioasq <file>`, review the ids and set `reviewed_by`, then
  `biolitsynth eval retrieval --bioasq <file> --split dev`. That gives D2. **D1** also needs the golden questions'
  anchor PMIDs, which a person picks.
- **Retrieval comparison proposal** (`docs/PROPOSAL_RETRIEVAL_COMPARISON.md`, 2026-11-05): Boolean deep vs
  full-corpus MedCPT vs hybrid, all cut to 25 after the same cross-encoder, with a pre-registered rule and two BioASQ
  sets drawn in advance (one created after MedCPT's release). Needs Tony's approval; it overrides §1.2 and replaces the
  D6 bucket-test gate. `eval select` gained `--created-after` (BioASQ ids carry their creation time).
- **Live check done 2026-11-05** with `gemini-3.8-flash` (picked from `models.list` as the newest stable Flash;
  `models.query` only, the other roles still hold placeholders). Results:
  - metformin/CRC, 2 sub-questions: both queries valid, all key concepts present, in band on the first draft
    (563 and 62 hits), 126 papers kept after dedup. Rerun: 0 LLM calls, 0 NCBI calls.
  - broad "effects of metformin": narrowed twice (3,898 → 3,617 → 2,183) and stopped at the cap just above the
    band; code kept the closest attempt as designed. The narrowing steps were weak (one swapped MeSH for `[ti]`),
    which is a D2 question for BioASQ, not something to tune by eye.
  - PubMed's own order for the "trials" query puts reviews and other-cancer trials near the top: the case for the
    P3 rerank (D4), to be measured, not assumed.
  - 6 replies recorded in `tests/fixtures/llm/query_builder_live_20261105.json`.
- **`providers.gemini.rpm: 10` is still a guess.** Check the free-tier limit for `gemini-3.8-flash` on AI Studio.
- **Rotate the Gemini and NCBI keys**: they were pasted into a chat session during the live check.
- **P2 acceptance is not met**: it needs the Lead's plan call (golden Q1 → 3 sub-questions with distinct facets),
  and §11.5 asks for D1-D2 on BioASQ dev, which needs the BioASQ subset and the retrieval-only eval.
- The scripted LLM replies in the tests were written by the coding agent. They are inputs (what a model might
  say), not expected answers, but replace them with recorded live replies once a live run exists.
