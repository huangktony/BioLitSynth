# Roadmap: weekly checkpoints

Companion to `docs/IMPLEMENTATION_PLAN.md` (the spec) and `docs/OPEN_QUESTIONS.md`. The plan says what to build.
This file says who builds it, by when, and how the two of you know a week is done.

**Assumptions (edit these first if wrong):**
- Demo is **Fri Dec 11, 2026**. Weeks start Monday. Nov 26 is Thanksgiving, so W4 is short.
- Two people, called **A** and **B** below. Write the names here: A = ____, B = ____.
- P0 is done (`3c00f09`, `05043a5`). Everything below starts from there.
- "Done" means a command or a person's check passes, never "the agent says it works".

## The honest summary

Five build weeks (W1 to W5) plus one rehearsal week for P1 to P7b. The pipeline (P1 to P6) fits. **The web app as
specified in §16 (FastAPI, SSE, React Flow, provenance drawer) does not fit.** Plan on the §12 fallback UI (form,
live stepper, report page with citation popovers) and decide at Gate 1 (Fri Nov 20) whether even that is realistic.
The verifier is the point of the project, so it is protected; the UI is what gets cut.

## At a glance

| Week | Dates | Phase | A (pipeline) | B (verification and data) | Check-in |
|---|---|---|---|---|---|
| W0 | Oct 26 - Nov 1 | Unblock | keys, model IDs | decisions, fixtures plan | Sun Nov 1 |
| W1 | Nov 2 - 6 | P1 | PubMed retrieval | quote validator, first checks, fixtures | Fri Nov 6 |
| W2 | Nov 9 - 13 | P2 | LLM clients, Lead, Query Builder | MedCPT spike, strength, more checks | Fri Nov 13 |
| W3 | Nov 16 - 20 | P3 | Extractor + live nodes | rerank metrics, thin-slice report | **Gate 1** Fri Nov 20 |
| W4 | Nov 23 - 25 | P4 | coverage, normalizer, adjudication | clusterer, conflict weights, cherry-pick scan | Wed Nov 25 |
| W5 | Nov 30 - Dec 4 | P5 + P6 | Synthesizer, Citation, assembler | Critic, router, retry loop, bake-off | **Gate 2** Fri Dec 4 |
| W6 | Dec 7 - 11 | Freeze | held-back eval, cache pre-warm | rehearsals, fallback UI polish | Demo Fri Dec 11 |

Hand-offs that block the other person (deliver by the day shown, or say so early):
- B's **draft schemas** (W1 Wed) unblock A's first prompts in W2.
- B's **quote validator** (W1 Fri) unblocks A's extractor wiring in W3.
- A's **LLM clients** (W2 Wed) unblock B's Critic in W5.
- A's first **live extraction output** (W3 Wed) unblocks B's thin-slice synthesis and fixtures.

## Every Friday (30 minutes, both people)

1. Both `git pull`, then `.venv/bin/pytest` is green and `.venv/bin/biolitsynth run "q" --dry` still walks the graph.
2. Each person runs their checkpoint command live for the other. Not a screenshot.
3. Anything under `tests/fixtures/` that changed this week gets read by the person who did not write it.
4. Update the status line in `CLAUDE.md` and add anything unclear to `docs/OPEN_QUESTIONS.md`.
5. Mark the week green, yellow (one item late) or red (checkpoint missed). **Red two weeks in a row, or a gate missed
   by more than two days, triggers the cut list at the bottom.** No debate that day.

---

## W0: Unblock (now to Sun Nov 1)

Goal: nothing in W1 or W2 waits on an account, a key, or an unanswered question.

- [ ] Both read `docs/OPEN_QUESTIONS.md` and agree or object to items 1 to 13.
- [ ] Get keys: Gemini (AI Studio), NVIDIA NIM (needs phone verification), NCBI API key. Put them in `.env` and refer
  to them by variable name only, never in the repo or in chat.
- [ ] Check current model IDs by hand on AI Studio and build.nvidia.com. Replace every `TODO-verify-*` in
  `config.yaml`. A model ID nobody has verified is the most likely thing an agent will invent.
- [ ] 1-hour structured-output test (scratch script, not committed): each candidate model, one real schema, 10 calls,
  count valid parses. This picks the critic provider before anyone writes critic code.
- [ ] Decide the draft-schema shape (OPEN_QUESTIONS, "required"). B writes them in W1.
- [ ] Decide `--resume` meaning (snapshots only is the suggestion) and whether the eval compares against simple
  baselines such as approve-all, flag-all and PMID-exists-only. The plan does not list baselines; adding them is a
  scope decision, so make it on purpose.
- [ ] Pick the four golden questions' **anchor PMIDs** by hand (papers that must survive reranking). A person picks
  them; they are test answers.

**Check-in:** keys work (one call per provider), `config.yaml` has zero `TODO-verify`, the parse-rate table exists.

## W1: P1 Retrieval (Nov 2 to 6)

**A:** `pubmed/` package (E-utilities client, XML parser, retraction and registry-ID flags), shared async token
bucket, PMID XML cache, esearch cache with a table, dedup + retraction filter wired into the `fetch` and `dedup` nodes,
`ncbi:` config block, `httpx` and `lxml` in `pyproject.toml`.

**B:** day 1 to 2, record real efetch XML with `curl` into `tests/fixtures/pubmed/`: a retracted paper, a structured
abstract with inline `<i>`/`<sup>` markup, a paper with no abstract, a `MedlineDate`. Then the draft schemas, the
quote-span validator (normalized position mapped back to the original abstract), and the deterministic checks that
need only schemas: `evidence_exists`, `pmid_in_run`, `quote_in_abstract`, `claim_type_rules`, `no_orphan_sentences`,
`retraction_guard`, `contested_both_sides`.

**Done when:**
- `pytest` offline: second run of the same query makes **0 network calls** (httpx `MockTransport` counting requests).
- Live, run by a person (`-m live`): a hand-written query fetches 25 papers into SQLite.
- The retracted fixture is dropped by dedup; the no-abstract fixture is logged as skipped.
- Property test: for every stored span, `abstract[start:end] == quote`, including dash and whitespace variants.
- Four deterministic injections from §11.4 are caught: fake evidence ID, unretrieved PMID, sentence with no claim
  marker, contested claim missing one side.

**Agent traps this week:**
- Parser reads `.text` and drops everything after the first inline tag. Require `itertext()` and the markup fixture.
- Offsets computed on the normalized string but checked against the original. The property test is the guard.
- A's parser tests pass because A recorded A's own fixtures. B records them, A tests against them.
- Rate limiter that works alone but not under `asyncio.gather` with real I/O. First real concurrency in the repo.

## W2: P2 Plan and Query (Nov 9 to 13)

**A:** `GeminiClient`, `OpenAICompatClient`, per-provider rate limit and 429 backoff, LLM cache, budget guard
(`BudgetExceeded`), `prompts/lead.v1.md` from Appendix A, Lead plan call, Query Builder with live `esearch` count
refinement (replaces the stub count).

**B:** MedCPT spike first: does it install and run on the demo laptop, and how long do 3 x 100 abstracts take
(record the number). Then the reranker wrapper, `FakeReranker`, strength rules (§4.3) as pure code, and the
remaining deterministic checks that need no live data: `inferred_multi_study`, `study_count_language`,
`conflict_addressed`, `balanced_no_winner`, `species_guard`, `direct_not_review_only`.

**Done when (all live ones run by a person):**
- Golden Q1 gives 3 sub-questions with distinct facets; every query lands in 20 to 2,000 hits.
- Re-running Q1 with unchanged prompts makes **0 LLM calls** (cache), and a prompt version bump invalidates only that stage.
- `BudgetExceeded` test: the graph marks the run `aborted_budget` and still assembles a partial result.
- MedCPT timing for 3 x 100 is written down, with the machine it was measured on. If it is too slow, say so now.
- Strength rules have a table-driven test for every row of §4.3.

**Agent traps this week:**
- Invented model IDs and out-of-date SDK calls. B reads A's client code against the current SDK docs.
- Cache key includes `run_id` or a timestamp, so it never hits and burns the free tier. Test key stability.
- Prompt asks the model for fields code owns (ids, offsets, strength). Use the draft schemas.
- Free-tier rate limits surprise only on the second run of the day. Log tokens and calls per run from the start.

## W3: P3 Rerank and Extraction, then Gate 1 (Nov 16 to 20)

**A:** extractor prompt (system prompt plus rendered user template, abstracts wrapped in `<abstract>` tags),
batching of 5, quote check wired to B's validator (one re-extract, then drop), real `fetch`, `dedup`, `rerank`,
`extract` nodes replacing the stubs.

**B:** reranker wired into the graph, `min_score` calibration, precision@25 and anchor-recall measurement on the
golden questions. Thin-slice path from §12's fallback: a basic Synthesizer, code-only citation resolution,
`report.md`, deterministic checks run over it. Record the live runs as fixtures.

**Gate 1 (Fri Nov 20).** Live, a person runs `biolitsynth run "<golden Q1>"` and gets a `report.md` where:
- every claim is cited and the deterministic checks ran over it;
- quote match is at least 95% on golden Q1 and Q2, and **zero** stored items lack a valid span;
- anchor-PMID recall after rerank is at least the recall before it, and precision@25 beats PubMed order;
- the recorded run is saved as the **dev set**. Golden Q3 and Q4 are set aside untouched as the **held-back set**.

If Gate 1 is missed by more than two days, drop to the cut list. This is the earliest point the fallback is cheap.

**Agent traps this week:**
- Extractor invents a quote that looks right. Only the code span check catches it; never relax it to make numbers look good.
- A batch of 5 abstracts gets findings attributed to the wrong PMID. Spot-check 20 items by hand.
- Cross-encoder scores are not comparable across sub-questions; a global threshold hides it.
- Mock-only green: if no test has ever hit real XML plus a real model output, the slice is not proven. Gate 1 is that proof.

## W4: P4 Coverage, Clustering, Conflicts (Nov 23 to 25, short week)

**A:** real coverage call and the one gap round (the node already owns the cap), term normalizer (one call, map only;
code applies it), conflict adjudication (sees evidence, never the weights).

**B:** study clusterer, conflict grouping and weighting with thresholds from `config.yaml`, cherry-pick scan, as pure
code with table-driven tests that sit on both sides of every threshold.

**Done when:**
- Golden Q2 (vitamin D and COVID-19) yields at least one confirmed conflict citing both sides, with a `balance` label
  and the weights visible in the output.
- A hand-made fixture with two PMIDs from one NCT trial produces one cluster; two items from one study never form a conflict.
- Retracted paper is never clustered; a large named cohort clusters as one study.
- Coverage: with the stub Lead replaced by a recorded real reply that still lists gaps in round 2, `coverage_rounds` stays 1.

**Agent traps this week:**
- Normalizer edits evidence directly instead of returning a map. Rule 1 in `CLAUDE.md`.
- Clusterer merges two different papers by the same first author. Needs a negative test.
- A "balanced" conflict summarized with a winner. Test the forbidden phrases, then read one output by eye.
- Short week: if time runs out, B's pure-code parts are the priority; adjudication can slip to W5 morning.

## W5: P5 Synthesis and Citation, P6 Verification, then Gate 2 (Nov 30 to Dec 4)

**A:** Synthesizer with all four claim types (prose carries claim ids only, never PMIDs), retry feedback that
regenerates only failed claims, Citation Agent LLM step, report assembler with the scope banner.

**B:** Critic on the provider chosen in W0 (sees only cited evidence plus the omitted evidence the scan found),
status rules in code, router and retry loop wired, the full §11.4 injection suite on **recorded** runs, then the model
bake-off on the dev set only, written to `docs/MODEL_EVAL.md`.

**Gate 2 (Fri Dec 4).**
- Every sentence in `report.md` is cited or classified as framing.
- All eight §11.4 injections are caught: deterministic ones at 100%, critic-judged ones at 85% or better.
- Retry loop stops after exactly 2 retries and passed claims do not change between attempts.
- `docs/MODEL_EVAL.md` names the chosen critic and extractor. **The held-back set was not used to choose them.**
- Rejected claims stay in the store and are listed in the report.

**Agent traps this week:**
- Critic status decided by the LLM instead of code. Status rules are code.
- Critic prompt quietly includes more than the cited evidence, so it judges with information the Synthesizer lacked.
- Claim ids renumbered between attempts; keys are `(claim_id, attempt)`.
- Injection tests that mutate a claim and re-run the same fake critic reply. The critic reply must be a recorded real one.
- Choosing models on the same data used for the final numbers inflates the catch rate.

## W6: Freeze and rehearse (Dec 7 to 11)

- **Mon Dec 7: feature freeze.** After that, only fixes.
- Held-back evaluation (golden Q3 and Q4): catch rate, false-flag rate, citation precision from a manual audit of 20
  random claim-to-evidence pairs per question, LLM calls and tokens per run. Report these numbers as they come out.
- Pre-warm the LLM and PubMed caches for the demo question.
- `biolitsynth demo <run_id>` replays a finished run with **0 API calls**. Test it with the network off.
- Two full rehearsals, one from a cold cache and one warm, plus one with the network disabled.
- Fallback UI polish only (form, stepper, report page with citation popovers), if Gate 1 kept it alive.

## Cut list (in this order, when a gate is missed)

1. Trace tab, Papers tab, React Flow pipeline view, SSE reconnect replay.
2. P8 beyond the eval command; resume UX.
3. The web app entirely: serve a static `report.html` plus `report.json` with a click-through provenance view.
4. Citation Agent LLM step (code-only resolution, per §12 fallback).
5. Term normalizer LLM call (seed synonym map only; say so in the known limitations).

**Never cut:** quote-span validation, the deterministic checks, the Critic, the §11.4 injection tests, the
held-back evaluation. If those go, there is no project to demo.
