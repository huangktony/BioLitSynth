# Decision results (plan §11.5)

One section per decision id. Each records the options, the data, the numbers, and what the pre-registered rule says.
A decision is final only when the team confirms it. Rows not yet run are `untested` and keep their default.

| ID | Status |
|---|---|
| D1 | untested (needs golden-question anchor PMIDs) |
| D2 | **run, provisional** (subset not yet reviewed by a person) |
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
