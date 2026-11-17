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
| P3 to P8 | Not started, except the MedCPT reranker module, built early for Track B (Tony, 2026-11-06). P2 is still not accepted |

What runs for real today (`biolitsynth run "<question>"`, no `--dry`):
question → **Lead** plan (2-4 sub-questions, facets, bridges) → **Query Builder** (LLM writes a PubMed query, code
refines it on live esearch counts, ≤2 refinements) → **fetch** → **dedup + retraction filter** → stop.
Everything after dedup (rerank, extraction, conflicts, synthesis, verification, report) is still a stub.

Also real but not wired into the graph yet (no real claims exist before P5):
- Quote span validator (`checks/quote_span.py`, §5.5).
- Seven schema-only deterministic checks (`checks/deterministic.py`, §5.9).
- BioASQ retrieval eval (`biolitsynth eval select | retrieval`, §11.3b).
- OpenAI-compatible client for NIM / Groq (no key yet, mock-tested only).
- MedCPT cross-encoder reranker (`rerank/`) and dense search over a local 35.9M-paper FAISS index
  (`retrieval/dense.py`), used only by `biolitsynth eval compare` (Track B). The graph's `rerank` node is still a stub.

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

## Reviewer feedback: Emmett Witchel, Tue Nov 10, 2026 (not yet addressed)

> Your main check depends on your system's own verification step, so it cannot show that the verifier itself is
> wrong. Pick two or three questions that a published systematic review already answers, and commit, before you build
> the pipeline, the conclusion each must reach and the PMIDs it must cite. Do the same for the contradiction case by
> naming the papers you plant and the disagreement the output must report. State what agent output you will reject,
> for example any conclusion whose cited abstract does not contain the supporting finding. Name the LLM you will use
> and record its responses and the PubMed results, so the demo and checks rerun without live API calls. Say how the
> two of you will split the work, and add a contact email.

He is right about the main gap: today every end-to-end check (Critic, §11.4 injection tests, Gate 2 catch rate) is
judged by our own pipeline, and the golden questions (§11.3) list only "expected properties" (e.g. "≥1 CONTESTED
claim"), not answers fixed in advance. Each point, what exists, and what is missing:

| # | He asks for | What exists now | Missing |
|---|---|---|---|
| 1 | 2-3 questions a published systematic review answers; the conclusion each must reach and the PMIDs it must cite, committed before the pipeline is built | Four golden questions (§11.3) with expected properties; anchor PMIDs planned but not picked (Open now). BioASQ expert labels, used today for retrieval only | The review-anchored questions. Must be committed **before P5 (Synthesizer) exists**, so nothing can be tuned toward it. BioASQ yes/no answers can check conclusions at scale (below) |
| 2 | The contradiction case: the planted papers and the disagreement the output must report | `--inject-conflict <pmid>` demo hook in the plan (§5.6); golden Q2 (vitamin D, COVID-19) is the intended conflict question | The planted PMIDs and the expected disagreement, written down and committed |
| 3 | What agent output we reject, e.g. a conclusion whose cited abstract lacks the supporting finding | Code-only checks: quotes must match the abstract verbatim (quote span validator, §5.5), seven deterministic checks (§5.9), §11.4 injection cases | One explicit, short reject list in one place, including checks that do not use our own LLM verifier. BioASQ expert snippets can check cited evidence (below) |
| 4 | Name the LLM; record its responses and PubMed results so the demo and checks rerun offline | Models in `config.yaml` (`gemini-3.8-flash` for Lead and Query Builder; extractor, synthesizer and critic still `TODO-verify-*`); SQLite LLM cache and PubMed cache; recorded Lead / Query Builder replies in `tests/fixtures/llm/` | The caches live in gitignored `data/`, so a fresh clone cannot replay the demo. Need a committed, recorded golden run that `pytest` and the demo can replay with no network |
| 5 | How the two of us split the work | Not written anywhere | A short owner list |
| 6 | A contact email | `ncbi.email_env` only (an env var for NCBI) | A team contact in `README.md` |

Proposed answers (people pick the content; per the CLAUDE.md fixture rule, the agent that writes the code must
not write these expected answers):
- [ ] **Systematic-review questions (Tony + teammate, before P5).** Pick 2-3 questions with a recent published
      systematic review or meta-analysis (Cochrane or similar). For each, commit to
      `tests/golden/review_anchored.yaml`: the question, the review's PMID, the conclusion the report must reach
      (one or two sentences, with direction and certainty), the PMIDs it must cite (the review's key included
      studies that have abstracts), and the date it was written. The golden questions in §11.3 can be reused if a
      review covers them. Checked by a person, and by a script for "required PMIDs cited".

      **Candidate reviews (found 2026-11-16 by a PubMed search; PMIDs checked, conclusions quoted from each
      abstract).** These are candidates only. A person still reads them, writes the expected conclusion in their own
      words, and picks the required PMIDs from the included studies.

      | Golden question | Fit | Candidate reviews (PMID, journal, year): what the abstract concludes |
      |---|---|---|
      | Q1 Metformin and cancer incidence in type 2 diabetes | Strong; also a contradiction case | **38291943** (J Natl Cancer Inst 2024, mostly observational): "may be associated with a decreased risk of many cancer types, but high heterogeneity and risk of publication bias limit confidence". **38389430** (Diabetes Obes Metab 2024, 27 RCTs, ~20,700 people): "did not reduce cancer incidence" (RR 1.07, 95% CI 0.87-1.31). **28031313** (Int J Epidemiol 2017, bias evaluation): the studies "least likely to be affected by bias did not support a causal effect" |
      | Q3 GLP-1 receptor agonists and pancreatitis | Strong; reviews mostly agree | **24736555** (BMJ 2014, 55 RCTs): "do not increase the risk of pancreatitis", evidence "not definitive". **32129106** (Expert Rev Clin Pharmacol 2020, 7 cardiovascular outcome trials, N = 55,932): no significant increase (OR 1.05, 95% CI 0.77-1.42). **40499738** (Gastroenterology 2025): no increase in pancreatitis; increases only for gallstones and GERD. **40988099** (Endocrinol Diabetes Metab 2025, 62 RCTs): "slightly increased risk", not significant when stratified by background medication |
      | Q2 Vitamin D supplementation and COVID-19 outcomes | Good; a second contradiction case | **34029377** (Cochrane 2021, living review): "insufficient evidence ... very uncertain". **35086394** (Expert Rev Anti Infect Ther 2022, 6 RCTs, 551 patients): beneficial (pooled RR 0.60, 95% CI 0.40-0.92). Check for a newer version of the Cochrane living review before committing |
      | Q4 Gut microbiome and Parkinson's disease progression | Weak: the reviews compare PD with controls and do not answer "progression" | **32557853** (Mov Disord 2020): Akkermansia increased; Roseburia and Faecalibacterium decreased in PD across countries. **40335465** (Nat Commun 2025, 4,489 samples): microbiome classifiers work within a study but generalise poorly across studies. Drop Q4 as review-anchored, or reword it to "how does the gut microbiome differ in PD" |

      Suggested use: Q1 and Q3 as the review-anchored questions; Q1 (observational benefit vs RCT null) or Q2
      (Cochrane "very uncertain" vs a small pooled benefit) as the contradiction case. The planted papers for the
      contradiction case should be primary studies picked from these reviews' included lists, one or more per side.
- [ ] **BioASQ answer checks (covers part of points 1 and 3 at scale).** BioASQ already holds expert answers fixed
      before our code existed, and we use only one field of them today. Each question has `documents` (relevant
      PMIDs, used now for recall), `exact_answer` (yes/no for 1,541 yes/no questions: 1,123 yes, 418 no),
      `ideal_answer` (an expert's short answer) and `snippets` (the exact abstract sentences that support the
      answer, with offsets). Two new checks, run on BioASQ yes/no questions not used for any tuning (draw a fresh
      subset with `eval select --exclude` against dev, held-back, check and both compare sets):
      - **Conclusion check:** the report's answer to the question matches `exact_answer`. Score: share correct, and
        a list of the misses for a person to read.
      - **Evidence check:** each cited quote overlaps an expert snippet from the same PMID (character offsets or a
        fuzzy text match). A cited quote that overlaps no snippet is flagged, not rejected outright, because BioASQ
        snippets are incomplete. A person reviews a sample of the flags.
      Limits: BioASQ questions are narrow single facts with one agreed answer, so they cannot test synthesis across
      many studies or the contradiction case, and the four golden demo questions are not in BioASQ. MedCPT may
      have seen older BioASQ data (see Track B). So these checks add to the review-anchored questions; they do not
      replace them.
- [ ] **Contradiction case.** In the same file: the PMIDs to plant with `--inject-conflict`, which side each is on,
      and the disagreement the report must state (a CONTESTED claim citing both sides, with the expected balance label).
- [ ] **Reject list** (add to the plan §6 and the report's "Rejected claims"):
      - a cited PMID that was not retrieved in this run (code);
      - a quote that is not in the cited abstract verbatim, after normalization (code, already built);
      - a claim whose cited quote does not state the finding, judged by a **person** on a sample of 20 claim-quote
        pairs per golden question (already §11's manual audit), not only by our Critic;
      - a conclusion that contradicts the committed review conclusion, or that omits a required PMID (script + person);
      - a CONTESTED claim that cites only one side, and any causal wording from observational-only evidence (code);
      - on BioASQ yes/no questions, an answer that contradicts `exact_answer` (script; counted, and each miss read
        by a person).
- [ ] **Recorded replay.** After the first full live golden run, export its LLM replies and PubMed responses to
      `tests/fixtures/golden_runs/<question>/` and add a replay mode, so `pytest` and the demo rerun with zero live
      calls. Name every model ID used in the README.
- [ ] **Work split** (Tony + teammate): one owner per track, e.g. retrieval + rerank + Track B vs extraction +
      synthesis + verification, and who writes the committed expected answers for the other person's code.
- [ ] **Contact email in `README.md`:** Tony to add the team address (an agent should not add a personal email).

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
- [~] `rerank/`: done for Track B (commit `49e3f5a`): cross-encoder wrapper with device select, `FakeReranker`,
      `select_top` (§5.4c keep/drop rules), score cache in a global `rerank_cache` table (not the run-scoped
      `relevance_scores`; the graph node should still write `relevance_scores` per run). License checked: public domain.
      Still to do: load the model once per process (singleton) and the fallback to PubMed order (§5.4c).
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

**Gate:** passed. Tony approved the comparison on 2026-11-06 ("add the medcpt, then implement the reranker, once
ready let's benchmark"). The proposal itself still says "proposal"; mark it approved when the results land.

**Pick up here:** B3. Everything before it is built and committed; no benchmark has run yet.

It compares, with everything else held
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

### B0 Verify the data (done 2026-11-06)
- [x] Chunks are `float32`, shape `(N, 768)`, `N` = `len(pmids_chunk_N.json)` (checked on every chunk by the build).
- [x] Vector norms are not 1 (sample of 5,000 from chunk 37: min 8.18, median 8.95, max 9.93). MedCPT scores by raw
      inner product, so the index uses inner product on unnormalized vectors.
- [x] License: US Government Work, public domain (`LICENSE` in github.com/ncbi/MedCPT; HF cards say
      `license_name: public-domain`). Cite Jin et al. 2023 wherever the index is published. MedCPT was trained on
      PubMed search logs; its paper reports zero-shot results on BEIR, which includes a BioASQ task, so treat older
      BioASQ questions as possibly seen (the recent set covers this).
- [x] Dump dates: chunks 0-36 2023-10-28, chunk 37 2024-03-24. Nothing newer is in the index.

### B1 Build the index once (done 2026-11-06)
- [x] `scripts/build_medcpt_index.py`: `IVF32768,PQ64`, inner product, trained on 2,000,000 vectors (400,000 from
      each of chunks 3, 12, 21, 30, 36), then all 38 chunks streamed in with `add_with_ids`. Built on a Kaggle CPU
      notebook in about 6 hours (training 1.7 h, ~6.5 min per chunk). faiss-cpu 1.15.1.
- [x] Result: **35,920,666 vectors, 2.69 GB**, at `data/medcpt/pubmed.ivfpq` + `data/medcpt/state.json` (build
      record: chunks, count, params, model, dump dates). sha256 `1964805fc51a96b264f98b90b15a3437c91ce835bac1fb8f80479e58f9fddad6`.
- [x] Copy on Hugging Face (private for now): `huangktony/medcpt-pubmed-ivfpq` (dataset repo, with a card and citation).
- [x] Build check (200 article vectors from chunk 18 as stand-in queries):

      | nprobe | own article in top 10 | exact top 10 in IVF-PQ top 1,000 (one chunk) | ms/query |
      |---|---|---|---|
      | 16 | 98.5% | 80.9% | 3.7 |
      | 64 | 98.5% | 91.7% | 5.0 |
      | 256 | 98.5% | 96.7% | 10.9 |

      **`nprobe` = 256** for the comparison, fixed here from the build check, before any BioASQ question is run.

### B2 Code for the comparison (done 2026-11-07, commit `49e3f5a`)
- [x] `biolitsynth/rerank/medcpt.py`: `CrossEncoderReranker` (lazy torch, `mps` on this Mac), `FakeReranker`,
      `query_text`, `article_text`, `select_top`, `score_with_cache` (cache key: model, sha256 of query, PMID).
- [x] `biolitsynth/retrieval/dense.py`: `DenseIndex.from_config` loads `data/medcpt/pubmed.ivfpq` into RAM (1.3 s;
      plain `read_index`, not `IO_FLAG_MMAP`), MedCPT query encoder (CLS vector, max 64 tokens), `nprobe` 256.
      `contains()` answers "is this PMID in the index"; the first call writes `data/medcpt/pubmed.ivfpq.pmids.npy`
      (287 MB) and later calls read it.
- [x] `biolitsynth/eval/compare.py` + `biolitsynth eval compare`. Per question: Query Builder → Boolean esearch top
      1,000 + dense top 1,000 → efetch the union → cross-encoder scores every fetched paper once → seven options cut
      from those lists: `boolean_order_25`, `dense_order_25` (no rerank, baselines), `boolean_500`, `boolean_1000`,
      `dense_200`, `dense_1000`, `hybrid` (Boolean 500 + dense 200). Reports recall@25, precision@25 (÷25), pool
      recall, relevant PMIDs in the index, relevant found only by each hybrid branch, timings, rerank pairs/s, and
      an estimate for 3 sub-questions. Writes `runs/<eval_id>/compare.json` and appends to `docs/eval_log.jsonl`.
- [x] Config: `rerank:` and `dense:` sections in `config.yaml`. Deps pinned: torch 2.14.1, transformers 5.20.2,
      faiss-cpu 1.15.1, numpy 2.5.4 (all released before 2026-10-23).
- [x] Tests (written by a separate agent from the spec, not the code author): `tests/unit/test_rerank.py`,
      `tests/unit/test_dense.py` (tiny FAISS indexes), `tests/integration/test_eval_compare.py` (hand-computed
      expected values in comments), `tests/slow/test_medcpt_models.py`. **334 offline tests pass.**
- [ ] The two slow tests have **not been run**: `.venv/bin/pytest -m slow`. They need a person to approve their
      inputs first: question "Does metformin use reduce the risk of colorectal cancer?", on-topic PMID 32968891,
      off-topic PMID 7997269 (also 32968891's exact title as the dense query).
- Smoke test on the laptop (real models): dense search k=1,000 in 0.05 s after the encoder loads (first call 3.4 s);
  cross-encoder ~48 pairs/s on `mps` with ~180-token texts (real abstracts are longer, so expect less). On-topic
  text scored +15.8, off-topic −16.0.
- Not done on purpose: the graph merge step (`retrieved_by: dense`), two-stage rerank, the HF auto-download. All
  wait for the B3 result.

### B3 Run and decide (next)
Setup (BioASQ file is local only, gitignored): `data/eval/bioasq/training14b.json`. Index: `data/medcpt/pubmed.ivfpq`
(or download `huangktony/medcpt-pubmed-ivfpq` from Hugging Face, private for now; ask Tony for access).

1. Smoke run, 2 questions, to check timing before the long run:
   `.venv/bin/biolitsynth eval compare --bioasq data/eval/bioasq/training14b.json --subset tests/golden/bioasq_compare_all.yaml --limit 2`
2. Main rows (Query Builder v1, pinned in `config.yaml`):
   `.venv/bin/biolitsynth eval compare --bioasq data/eval/bioasq/training14b.json --subset tests/golden/bioasq_compare_all.yaml`
   `.venv/bin/biolitsynth eval compare --bioasq data/eval/bioasq/training14b.json --subset tests/golden/bioasq_compare_recent.yaml`
3. Secondary row: the same two commands with `--prompt-version 2` (only the Boolean options matter for it).
- Cost: about 1-3 Gemini calls per question (Query Builder only), 100-300 per pass; the free tier may need two
  days. Everything is cached (LLM replies, esearch, abstracts, rerank scores), so a crashed run resumes cheaply.
- Time: rerank dominates. Up to ~2,000 candidates per question at an estimated 25-50 pairs/s is ~1-1.5 min per
  question, so ~1-2 h per 50-question set on the first pass. The v2 pass reuses most scores.
- One run per option, no tuning between runs (proposal §4). `nprobe` 256 was fixed in B1, before any question ran.
- [ ] Record all rows in `docs/MODEL_EVAL.md` and apply the proposal's rule:
      (a) hybrid recall@25 ≥ best Boolean + 10 points on the any-date set; (b) not lower on the recent set;
      (c) `est_hybrid_3_subq_s` ≤ 120 s; (d) index ≤ 5 GB (met: 2.69 GB + 287 MB id cache).
- [ ] If adopted: config `retrieval: {mode: hybrid, ...}`, merge step in the graph (`merge_unique` reducer on any
      key written by parallel branches), code-built Boolean branch with a recency clause from the dump date, gap
      signal, HF download on first use with Boolean-only fallback, plan v1.6 (§1.2, §1.3, §5.2-5.4c, §11.5, §3
      diagram), make the HF dataset public (Tony's call; cite MedCPT).
- [ ] If not adopted: keep Boolean, set the candidate depth from D3, close Track B. The reranker stays (P3 needs it).

### Track B loose ends
- `KMP_DUPLICATE_LIB_OK=TRUE` is set in `biolitsynth/__init__.py`: torch and faiss-cpu each ship their own libomp on
  macOS and the process aborts without it. Import order did not help. Revisit if a later wheel fixes it.
- The index stops at the NCBI dump (chunks 0-36: 2023-10-28, chunk 37: 2024-03-24). The recent set will show it.
- `scripts/build_medcpt_index.py` rebuilds the index (~6 h on a Kaggle CPU notebook, ~115 GB streamed, peak ~9 GB disk).

## Rules that bite

- Fixtures are recorded from live runs or written by a person. The agent that writes code never writes its expected
  answers. Scripted LLM replies in tests are inputs; replace them with recorded replies when they exist.
- Tests are offline: they never read `.env` (`BIOLITSYNTH_NO_DOTENV`), and fast limiters are injected.
- Every LLM call goes through `StageLLM` (span, cache, budget, rate limit). Every NCBI call through `PubMedClient`.
- Model ids come from `config.yaml` only, verified by hand against the provider's model list.
- Prompts are versioned files; change one by adding `v<N+1>`, never by editing the live version.
- Decisions follow §11.5: pre-register the rule, tune on dev only, held-back data once at the end.
- Ask before `git push`.
