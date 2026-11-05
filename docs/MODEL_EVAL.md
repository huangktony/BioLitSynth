# Decision results (plan §11.5)

One section per decision id. Each records the options, the data, the numbers, and what the pre-registered rule says.
A decision is final only when the team confirms it. Rows not yet run are `untested` and keep their default.

Every eval run also appends one machine-written line to `docs/eval_log.jsonl` (question ids, model, prompt
version and hash, config hash, git commit, summary and per-question recall; no BioASQ text). The dashboard's
Eval tab (History card) reads it. That log is the raw record; this file is where decisions are written up.

| ID | Status |
|---|---|
| D1 | untested (needs golden-question anchor PMIDs) |
| D2 | **run, provisional** (subset not yet reviewed by a person) |
| QB-v2 | **run 2026-11-05: not adopted** (fails rules a and b); `query_builder` stays pinned to v1 |
| RC | **proposed** (retrieval comparison, `docs/PROPOSAL_RETRIEVAL_COMPARISON.md`); rule fixed there; needs Tony's approval |
| D3-D14 | untested |

---

## D2: Query hit-count refinement (band 20-2,000, ≤2 refines)

- **Date:** 2026-11-05 · **Eval:** `runs/eval_20261105_231810_retrieval_dev/retrieval.json` (gitignored)
- **Data:** BioASQ dev, 30 questions from `training14b.json` (sha256 `5669afbc…`), subset
  `tests/golden/bioasq_subset.yaml` (seed 0). **`reviewed_by` is still empty:** the run happened before a person
  reviewed the subset, so the result is provisional until the review confirms the same 30 questions.
- **Setup:** each question is one sub-question (no key concepts), `gemini-3.8-flash`, `query_builder.v1`,
  `retmax_candidates` 100, config sha256 `b0db207d5591`.
- **Cost:** 32 LLM calls, 88 NCBI calls, 5 min 47 s.

| Metric | First LLM draft | After refinement |
|---|---|---|
| Mean recall@fetch (top 100) | 0.288 | 0.288 |
| Queries in band | 27 / 30 | 30 / 30 |
| Questions refined | | 3 (5 refinements) |

The 3 refined questions (draft → final hits): fibrocyte 2,192 → 563, RTC 9 → 28, bisphenol A 7,372 → 171. In each
case recall was identical before and after (0.29, 0.80, 0.00).

**Rule (§11.5):** keep refinement only if it adds ≥3 points of recall@fetch; otherwise drop the loop. **Result:
+0.0 points**, so the rule says drop it. Caveat: only 3 of 30 questions triggered refinement, so the comparison rests
on 3 questions. Refinement costs 1 extra LLM call and 1 esearch per attempt, and here it bought band compliance only.
**Decision pending** the team's confirmation and the subset review.

### Observations for later decisions (not decisions)
Of 529 relevant PMIDs across the 30 questions:
- **26%** were in the final query's top 100.
- **38% beyond the cap:** the query matches them, but PubMed ranks them past 100. Measure in **D3** (100 vs 200) and
  **D4** (rerank), which address exactly this.
- **36% not matched** by the final query. D6 Stage 0 (P3) splits these into "a broader query finds it" (c) and
  "no Boolean query finds it" (d). Bucket d decides whether the hybrid track starts.
- 7 of 30 questions had zero recall. Two patterns, to check on more data rather than tune by eye:
  definition-style questions where wording leaks into the query ("Define lncRNA." required `definition[tiab]`),
  and over-narrow first drafts (bisphenol A restricted to `[ti]` fields).
- BioASQ labels for older questions reflect PubMed at the time they were written; newer papers can outrank them,
  which inflates "beyond cap".

---

## QB-v2: Query Builder prompt v1 vs v2 (pre-registered before v2 was written)

**Why:** a diagnostic on the D2 dev run (NCBI only, no LLM) found that 171 of the 189 relevant PMIDs the final query
did not match were excluded by a **single** AND-group, usually the question's framing words made into a required
concept ("function", "definition", "adverse effects"), a title-only field, or a population/design filter the
question did not need. v2 changes the prompt's rules for those failure **categories**.

**Guards against fitting the dev set:**
1. v2 is written once. If it fails this rule, the result is recorded and v2 is not adopted. A v3 would need a new,
   unseen check set.
2. v2's rules name failure categories, not questions. Its examples do not come from BioASQ.
3. A fresh **check set** (`tests/golden/bioasq_check.yaml`, 30 questions, seed 1, no overlap with dev or
   held-back) is drawn and committed **before** v2 exists. The held-back set stays untouched.
4. `config.yaml` pins `prompt_versions: {query_builder: 1}`; v2 goes live only if the rule below passes.

**Data:** BioASQ dev (30) and the check set (30), `gemini-3.8-flash`, `retmax_candidates` 100, everything else as in D2.

**Rule (all must hold to adopt v2):**
- (a) mean recall@fetch on **dev** rises by **≥ 3 points** over v1 (v1 dev = 0.288, from the D2 run);
- (b) mean recall@fetch on the **check set** rises by **≥ 3 points** over v1 on the same check set;
- (c) at least **90%** of final queries land in the 20-2,000 band on each set;
- (d) golden Q1 (live, Lead plan) still gives 3 queries in band that each contain every key concept.

If (a) holds but (b) does not, the gain is treated as fitting the dev set and v2 is not adopted.

### Result (2026-11-05)

Runs (gitignored): dev v1 `eval_20261105_231810`, dev v2 `eval_20261105_233436`, check v1 `eval_20261105_234218`,
check v2 `eval_20261105_234823`. 147 LLM calls in total.

| | dev v1 | dev v2 | check v1 | check v2 |
|---|---|---|---|---|
| Mean recall@fetch (top 100) | 0.288 | 0.301 | 0.470 | 0.458 |
| Queries in band | 100% | 97% | 97% | 93% |
| Relevant PMIDs matched by the final query at all | 64% | 70% | 75% | 78% |
| ...of which in the top 100 | 26% | 27% | 37% | 36% |
| ...ranked past 100 (beyond cap) | 38% | 42% | 38% | 42% |
| Not matched | 36% | 30% | 25% | 22% |
| Median final hit count | 253 | 383 | 171 | 269 |

| Rule | Needed | Got | |
|---|---|---|---|
| (a) dev recall@fetch | ≥ +3 pts | +1.3 | **fail** |
| (b) check-set recall@fetch | ≥ +3 pts | −1.2 | **fail** |
| (c) in band on each set | ≥ 90% | 97%, 93% | pass |
| (d) golden Q1 | | not run (a and b already fail) | |

**Decision: v2 is not adopted.** `config.yaml` keeps `prompt_versions: {query_builder: 1}`. `prompts/query_builder.v2.md`
stays in the repo, unused, as the record of what was tested. As pre-registered, no further edits were tried.

**What the numbers show (observations, not a new decision):** v2 did what it was written to do. It matched more of
the relevant papers on both sets (+6 and +3 points), and it lowered the not-matched share on the unseen check set
too, so the effect is not specific to the dev questions. But the extra matches landed **past PubMed's top 100**:
removing framing groups made queries broader (median hits up ~50%), PubMed's lexical ranking put the newly matched
papers low, and recall@100 did not move. So v2's value cannot be judged with a 100-paper cap and no rerank.

**What follows:**
- Re-testing v2 belongs in P3, after D3 (cap 100 vs 200) and D4 (MedCPT rerank), with **recall@25 after rerank** as
  the metric. That is a new comparison under a different pipeline, so it gets its own pre-registered rule and a
  **new unseen check set** (the current one has now been seen by v2).
- For D6 (hybrid): with v2, 70-78% of relevant papers are matched by a Boolean query, and the not-matched share fell
  further when only framing groups were removed. The case for dense retrieval rests on what remains in bucket d
  after P3, not on today's 22-36% not-matched figures.
- The code-side check (`concept_warnings`, trace event `query.group_not_a_key_concept`) is kept: it flags framing
  groups in live runs, where the Lead supplies key concepts, at no cost.
