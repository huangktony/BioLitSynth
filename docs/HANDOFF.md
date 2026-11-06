# Handoff and TODO (Thu Nov 5, 2026, updated after the BioASQ runs)

Read this first, then `docs/OPEN_QUESTIONS.md` ("P2 status"), then `docs/IMPLEMENTATION_PLAN.md` v1.5 (§11.3b, §11.5,
§12). `docs/ROADMAP.md` still has the weekly calendar; where it and this file disagree on status, this file is newer.

There are two task tracks below:
- **Track A, Boolean only:** the current plan. PubMed Boolean queries → MedCPT cross-encoder rerank → extraction.
  This is what gets built.
- **Track B, Hybrid:** adds a dense MedCPT branch over all of PubMed. Proposed as a **direct head-to-head comparison**
  (`docs/PROPOSAL_RETRIEVAL_COMPARISON.md`) instead of the old bucket-test gate. Nothing in Track B is built until
  Tony approves the proposal.

---

## Where things stand

| Phase | Status |
|---|---|
| P0 Skeleton | Accepted |
| P1 Retrieval | Accepted |
| P2 Plan + Query | **Code complete, waiting for acceptance.** BioASQ dev run done: D2 provisional, QB-v2 not adopted |
| P3 to P8 | Not started. Do not start P3 until Tony says "accepted" for P2 |

What runs for real today (`biolitsynth run "<question>"`, no `--dry`):
question → **Lead** plan (2-4 sub-questions, facets, bridges) → **Query Builder** (LLM writes a PubMed query, code
refines it on live esearch counts, ≤2 refinements) → **fetch** → **dedup + retraction filter** → stop.
Everything after dedup (rerank, extraction, conflicts, synthesis, verification, report) is still a stub.

Also real but not wired into the graph yet (no real claims exist before P5):
- Quote span validator (`checks/quote_span.py`, §5.5).
- Seven schema-only deterministic checks (`checks/deterministic.py`, §5.9).
- BioASQ retrieval eval (`biolitsynth eval select | retrieval`, §11.3b).
- OpenAI-compatible client for NIM / Groq (no key yet, mock-tested only).

BioASQ results so far (`docs/MODEL_EVAL.md`): Query Builder v1 reaches mean recall 0.29 (dev) / 0.47 (check set) in
PubMed's top 100. Refinement added nothing (D2, provisional). Prompt v2 matched more relevant papers but not in the
top 100, so it was not adopted; `config.yaml` pins `query_builder: 1`. The queries match 70-86% of relevant papers
at any depth, but only 10-15% are in PubMed's top 25: **ranking is the main loss**, which P3 and the proposal target.

Live check, golden Q1 (2026-11-05, `gemini-3.8-flash`): 3 sub-questions with distinct facets (clinical_outcome,
mechanism, epidemiology), 1 bridge, queries at 127 / 469 / 417 hits on the first draft, 271 papers after dedup.
A rerun makes 0 LLM and 0 NCBI calls. 263 offline tests pass in about 3 seconds.

## Set up in 2 minutes

    uv venv --python 3.13 .venv && uv pip install -p .venv/bin/python -e '.[dev]'
    .venv/bin/pytest

Create `.env` in the repo root (gitignored, never commit it, never paste keys in chat):

    GEMINI_API_KEY=...
    NCBI_API_KEY=...
    # NVIDIA_API_KEY=...   # NIM critic, P6

Then:

    .venv/bin/biolitsynth run "Does metformin reduce cancer incidence in patients with type 2 diabetes, and through what mechanisms?"
    .venv/bin/biolitsynth run "q" --dry          # whole graph on stubs, no network

Output: `runs/<run_id>/` (plan and query snapshots, `trace.jsonl`) and `data/biolitsynth.db`. Both are gitignored.

## Open now (people, not code)

- [ ] **Rotate the Gemini and NCBI keys.** They were pasted into a chat session on Nov 5. Put the new ones in `.env`.
- [ ] **Tony reviews** plan v1.5 (BioASQ + decision register), the P2 demo above, and items 22-39 in OPEN_QUESTIONS.
- [ ] **BioASQ:** register, download the Task B training set into `data/eval/bioasq/`, check the license.
- [ ] **BioASQ subset:** `biolitsynth eval select --bioasq <file>`, then a person reviews the 50 ids, may drop
      off-topic ones, and sets `reviewed_by` in `tests/golden/bioasq_subset.yaml`. Done before any tuning.
- [ ] **Golden-question anchor PMIDs** (3-5 per golden question), picked by a person. Needed for D1, D4 and Gate 1.
- [ ] **Gemini free-tier limit** for `gemini-3.8-flash`: set `providers.gemini.rpm` from AI Studio (10 is a guess).
- [ ] **NVIDIA NIM key** (phone verification) and a verified critic model id, before P6.
- [ ] **Confirm the §11.5 thresholds** (3 / 5 / 10 points, 95% / 98%) before their experiments run.
- [ ] **Review the BioASQ dev subset** (`tests/golden/bioasq_subset.yaml`) and set `reviewed_by`. It was used before
      review, so D2 is provisional until the review keeps the same 30 questions (or the eval is rerun).
- [ ] **Tony decides on `docs/PROPOSAL_RETRIEVAL_COMPARISON.md`** (overrides §1.2 and the D6 gate; widens D3).
- [ ] **Tony confirms or overrides D2** (the rule says drop query refinement: +0.0 points on BioASQ dev).

---

## Track A: Boolean only (the current plan)

Each phase ends with: tests green, the acceptance demo run live for Tony, a commit, then stop until "accepted".

### P2 acceptance (finish now)
- [x] `biolitsynth eval retrieval --bioasq data/eval/bioasq/training14b.json --split dev` → **D2** recorded in
      `docs/MODEL_EVAL.md` (provisional; refinement +0.0 points).
- [x] QB-v2 prompt tested on dev + an unseen check set under a pre-registered rule: **not adopted**.
- [ ] **D1** (2 / 3 / 4 sub-questions vs the rubric): needs anchor PMIDs. Add a small golden-question eval that
      reports anchor recall over all fetched papers per setting.
- [ ] Demo to Tony: golden Q1 live (above) + the BioASQ dev summary.

### P3 Rerank + Extraction (Gate 1, Fri Nov 20)
- [ ] `rerank/`: MedCPT cross-encoder wrapper (singleton, device select, `relevance_scores` cache, `FakeReranker`),
      fallback to PubMed order if the model can't load (§5.4c). New deps: `torch`, `transformers`. Check the license.
- [ ] Real `rerank` node: top-X per sub-question, `kept` / `drop_reason` stored, never deleted.
- [ ] Extend the retrieval eval with **recall@25 after rerank** and precision@25 (the main §11.3b metric).
- [ ] **D3** (100 / 200, and 500 / 1,000 if the proposal is approved), **D4** (rerank on/off, X = 25 vs 50, share
      truncated at 512 tokens), **D5** (`min_score` from BioASQ positives + ~50 person-labeled pairs).
- [ ] Two-stage rerank (article-encoder similarity → top 100 → cross-encoder) if the cross-encoder alone is too slow
      on deep pools.
- [ ] **D6:** if the proposal is approved, Track B's comparison replaces the bucket test. If not, run the bucket test
      as §11.5 describes (broad code-built query from `key_concepts`, split `not_matched` into buckets c and d).
- [ ] Extractor: `prompts/extractor.v1.md`, an `EvidenceDraft` schema (no ids, offsets, strength, study_id),
      batches of 5, wired to the quote validator (one re-extract, then drop, log `validation.quote_*`).
- [ ] Strength rules in code (§4.3). Seed `StudyDesign` from publication types (deferred from P1, item 18).
- [ ] Set `models.extractor` from `models.list` (newest Flash-Lite) and record live extractor replies as fixtures.
- [ ] **D7** (batch 5 vs 8), **D8** (fuzzy threshold 90 / 95 / 98, snaps judged by a person).
- [ ] Thin-slice report for Gate 1: basic Synthesizer, code-only citations, `report.md`, the existing
      deterministic checks run over it. Golden Q1 and Q2 recorded as the **dev set**; Q3 and Q4 untouched (held back).

### P4 Coverage + Clustering + Conflicts (Wed Nov 25)
- [ ] Lead Call 2 (coverage): add `<!-- user:coverage -->` to the Lead prompt as `lead.v2.md`, `CoverageDecision`
      draft, real `coverage` node (the node already owns the round cap).
- [ ] Study clusterer (§5.4b): registry id, duplicate flag, named cohort, author + n + year.
- [ ] Term normalizer (§5.5b): `config/synonyms.yaml` seed + one LLM call, conservative merges.
- [ ] Conflict detector (§5.6): grouping and weights in code, LLM adjudication, balance by code.
- [ ] **D9-D12** recorded (gap round value, clusterer rule precision, merge precision, balance thresholds).

### P5 Synthesis + Citation
- [ ] Synthesizer (§5.7), claim ids only in prose (`[cl4]`, OPEN_QUESTIONS item 1), regenerate only failed claims.
- [ ] Citation Agent (§5.8): code resolves claim → evidence → PMID + quote; one LLM call for unmarked sentences.
- [ ] Report Assembler (§5.12): define `FinalReport`, scope banner, `report.md` + `report.json`.
- [ ] Decide `--resume` semantics (OPEN_QUESTIONS, "Not decided yet").

### P6 Verification (Gate 2, Fri Dec 4)
- [ ] Wire `checks/deterministic.py` into `det_checks`; add the P4-dependent checks (`inferred_multi_study`,
      `study_count_language`, `conflict_addressed`, `balanced_no_winner`, `cherry_pick_scan`,
      `direct_not_review_only`, `species_guard`).
- [ ] Critic (§5.10) on a different model family via `OpenAICompatClient`; status rules in code.
- [ ] Full §11.4 injection suite on **recorded** runs; **D13** (model bake-off) and **D14** (retry count).

### P7 Web app (scope decided at Gate 1)
- [ ] P7a: FastAPI routes, run manager, SSE events, query-approval resume (fix `approve_queries`, OPEN_QUESTIONS).
- [ ] P7b: fallback UI first (form, live stepper, report with citation popovers), full §16 only if time allows.

### P8 / W6 Freeze
- [ ] `biolitsynth eval` prints §11.3b + §11.4 metrics; run the held-back sets **once**.
- [ ] Pre-warm caches; `biolitsynth demo <run_id>` with the network off.

---

## Track B: Hybrid (Boolean + dense MedCPT), via a direct comparison

**Gate to start:** Tony approves `docs/PROPOSAL_RETRIEVAL_COMPARISON.md`. It compares, with everything else held
fixed (same Query Builder, same cross-encoder, same cut to 25 papers):
1. **Boolean, deep:** top 500 / 1,000 by PubMed order → cross-encoder → top 25.
2. **Full-corpus MedCPT:** top 200 / 1,000 from an IVF-PQ index over ~37M PubMed vectors → cross-encoder → top 25.
3. **Hybrid:** union of 1 (top 500) and 2 (top 200) → cross-encoder → top 25.

Adopt the hybrid only if recall@25 beats the best Boolean setting by **≥ 10 points** on the any-date set, is **not
lower** on the post-2023 set, retrieval for 3 sub-questions takes **≤ 2 minutes**, and the index is **≤ 5 GB**
(full rule in the proposal, §4). The two comparison sets are already drawn and committed:
`tests/golden/bioasq_compare_all.yaml` (50) and `tests/golden/bioasq_compare_recent.yaml` (50, created ≥ 2023-08-01).
Neither overlaps dev, held-back or the check set.

Depends on: P3's cross-encoder (all options use it). Runs alongside the extractor work.

### B0 Verify the data (minutes, a few KB)
- [ ] Header check on one chunk: `curl -s -r 0-127 <base>/embeds_chunk_0.npy | strings` → expect `'<f4'`, `(~950000, 768)`.
- [ ] `len(pmids_chunk_0.json) == rows`. Check vector norms on a sample (inner product, normalize or not).
- [ ] Confirm in the MedCPT README that the vectors come from the same `ncbi/MedCPT-Article-Encoder` as the
      Hugging Face weights, check the license, and check whether MedCPT was trained or evaluated on BioASQ.
- [ ] Record the dump date: chunks 0-36 are Oct 2023, chunk 37 Mar 2024, so the index ends around early 2024.

### B1 Build the index once (local or a temporary cloud VM; same script)
- [ ] `scripts/build_medcpt_index.py` (outside the package): train IVF-PQ (`nlist` 32768, `m` 64) on a ~2M-vector
      sample spread across chunks, then stream chunks one at a time with `add_with_ids(vectors, pmids)`, deleting each
      chunk after. Peak disk ~10-15 GB, not 103 GB. Output `pubmed_ivfpq.faiss` (~2.5-3 GB) + `index_meta.json`
      (model, dump date, params, count).
- [ ] Where: laptop (download ~103 GB over a few hours) or a temporary VM near NCBI (e.g. GCP `us-east4`,
      `n2-standard-8`, 100 GB disk), copy the index down, delete the VM. Nothing stays hosted.
- [ ] Store at `data/medcpt/` (gitignored). New dep: `faiss-cpu` (pin an exact version at least 2 weeks old).

### B2 Code for the comparison
- [ ] `retrieval/dense.py`: load the index with `IO_FLAG_MMAP`, MedCPT query encoder (shared model loading with the
      reranker), `search(sub_question_text, k, nprobe=32) → PMIDs`. Trace span per search.
- [ ] Merge step: union per sub-question, `retrieved_by` gains `dense` / `boolean`, then the existing efetch →
      dedup → rerank. A parallel-written key needs the `merge_unique` reducer (CLAUDE.md graph rules).
- [ ] Eval: `biolitsynth eval retrieval --mode boolean|dense|hybrid --depth N`, reporting recall@25 after rerank,
      precision@25, pool recall, unique relevant per branch, wall time, and "relevant PMIDs in the index".
- [ ] Tests: a tiny FAISS index built in the test from random vectors with known nearest neighbours; merge and
      provenance tests on the mock NCBI. No real index in CI.

### B3 Run and decide
- [ ] Both comparison sets, all three options (and Query Builder v2 on option 1 as the secondary row). One run per
      option, no tuning between runs. Record everything in `docs/MODEL_EVAL.md`.
- [ ] Apply the proposal's rule. If the hybrid is adopted: config `retrieval: {mode: hybrid, ...}`, code-built
      Boolean branch with a recency clause from the dump date, gap signal (Boolean 0 hits and no dense result above
      `min_score`), update plan §1.2, §1.3, §5.2-5.4c, §11.5 and the §3 diagram, bump to v1.6.
- [ ] If not adopted: keep Boolean, set the candidate depth from D3, close Track B.

## Rules that bite

- Fixtures are recorded from live runs or written by a person. The agent that writes code never writes its expected
  answers. Scripted LLM replies in tests are inputs; replace them with recorded replies when they exist.
- Tests are offline: they never read `.env` (`BIOLITSYNTH_NO_DOTENV`), and fast limiters are injected.
- Every LLM call goes through `StageLLM` (span, cache, budget, rate limit). Every NCBI call through `PubMedClient`.
- Model ids come from `config.yaml` only, verified by hand against the provider's model list.
- Prompts are versioned files; change one by adding `v<N+1>`, never by editing the live version.
- Decisions follow §11.5: pre-register the rule, tune on dev only, held-back data once at the end.
- Ask before `git push`.
