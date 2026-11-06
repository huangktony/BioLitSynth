# Proposal: retrieval head-to-head (Boolean deep vs full-corpus MedCPT vs hybrid)

**Status:** proposal, 2026-11-05. **Needs Tony's approval** before any code for it is written: it overrides the
§1.2 non-goal "dense retrieval over our own corpus" and replaces the D6 gate in §11.5. Nothing in the plan text
changes until it is approved.

**Ask:** approve one direct comparison of three retrieval options, measured the same way, with a decision rule fixed
now. If approved, it runs in P3, after the MedCPT cross-encoder exists (all three options need it).

---

## 1. Why

BioASQ dev and check sets, Query Builder v1 and v2, recall by PubMed-order depth (mean per question):

| | top 25 | top 100 (now) | 200 | 500 | 1000 | all the query matches |
|---|---|---|---|---|---|---|
| dev, v1 | 0.10 | 0.29 | 0.45 | 0.62 | 0.68 | 0.70 |
| check, v1 | 0.12 | 0.48 | 0.65 | 0.78 | 0.81 | 0.82 |
| dev, v2 | 0.11 | 0.30 | 0.44 | 0.68 | 0.72 | 0.73 |
| check, v2 | 0.15 | 0.46 | 0.66 | 0.83 | 0.86 | 0.86 |

That recall grows with depth is expected and proves nothing on its own: extraction always gets 25 papers, so what
matters is whether a reranker can pull relevant papers out of a bigger pool. What the table does show is where papers
are lost:
- **Ranking:** Boolean queries already match 70-86% of relevant papers, but PubMed's order puts only 10-15% in the
  top 25. Roughly 55-70 points sit between "matched" and "reaches extraction".
- **Matching:** 14-30% of relevant papers are matched by no query we wrote.

A deeper Boolean pool plus rerank targets the first loss. Full-corpus MedCPT targets both at once: it ranks by
meaning across all of PubMed instead of inside a Boolean result set. Which is better is an empirical question.
The bucket test in §11.5 D6 would only estimate whether dense retrieval *could* help; this comparison measures
whether it *does*, so it replaces that gate.

Full record of the runs behind these numbers: `docs/MODEL_EVAL.md` (D2, QB-v2).

## 2. The three options

Everything except candidate retrieval is held fixed: Query Builder pinned to `query_builder.v1` (and v2 as a
secondary row, §5), the same MedCPT cross-encoder, the same cut to **25 papers per sub-question**, the same questions.

| # | Option | Candidates per sub-question | Then |
|---|---|---|---|
| 1 | **Boolean, deep** | top 500 and top 1,000 by PubMed order (two settings) | cross-encoder → top 25 |
| 2 | **Full-corpus MedCPT** | top 200 and top 1,000 from an IVF-PQ index over ~37M PubMed vectors | cross-encoder → top 25 |
| 3 | **Hybrid** | union of option 1 (top 500) and option 2 (top 200) | cross-encoder → top 25 |

- If a cross-encoder over 1,000+ candidates is too slow on the demo laptop, all options use the same two-stage rerank:
  MedCPT article-encoder similarity to cut to 100, then the cross-encoder. Same rerank for every option, or the
  comparison isn't fair.
- Option 2 reports two things separately: its recall, and the share of relevant PMIDs that are in the index at all
  (the embedding dump stops around early 2024).
- Dense-only (2) is measured for understanding, not proposed for production: it cannot enforce filters, exact ids
  or negation, cannot signal a real gap (it always returns results), and misses everything after the dump.

## 3. Data (drawn and committed before any comparison code exists)

| Set | File | Size | Drawn |
|---|---|---|---|
| Comparison, any date | `tests/golden/bioasq_compare_all.yaml` | 50 | seed 2, excludes dev, held-back and check |
| Comparison, recent | `tests/golden/bioasq_compare_recent.yaml` | 50 | seed 3, created ≥ 2023-08-01, excludes all of the above |

- Both use the §11.3b rules (`yesno` / `summary`, ≥ 5 relevant PMIDs). No overlap between any two subsets.
- **Why a recent set:** MedCPT (2023) may have been trained or evaluated on older BioASQ questions, which would flatter
  options 2 and 3. Questions created after its release can't have been seen. BioASQ ids are MongoDB ObjectIds, so the
  creation date is in the id. 240 eligible unused questions were created after 2023-08; 50 were drawn.
- **The catch:** recent questions may cite papers newer than the embedding dump, which handicaps options 2 and 3. So
  both slices are reported, and neither is picked after the fact.
- The dev, held-back and check sets are not used here.

## 4. Pre-registered decision rule

Main metric: **mean recall@25 after rerank**. Also reported: precision@25 (a lower bound, since BioASQ labels are
incomplete), recall of the candidate pool before rerank, relevant papers found only by each branch of option 3,
retrieval wall time for 3 sub-questions on the demo laptop, disk and RAM.

**Adopt the hybrid (3) only if all of these hold:**
- (a) recall@25 beats the better setting of option 1 by **≥ 10 points** on the any-date set;
- (b) recall@25 is **not lower** than option 1's on the recent set;
- (c) retrieval for 3 sub-questions takes **≤ 2 minutes** on the demo laptop, rerank included;
- (d) the index and its metadata fit in **≤ 5 GB** on the demo laptop.

**Otherwise** keep Boolean (1). Pick its depth as the smallest of {100, 200, 500, 1,000} whose recall@25 is within 3
points of the best (this is D3, widened past the §1.3 cap of 200; see §6).

If (a) holds but (b) fails, treat the gain as an artefact of MedCPT having seen older BioASQ data and do not adopt.

## 5. Secondary row: Query Builder v2

v2 matched more relevant papers than v1 but lost at depth 100 (MODEL_EVAL, QB-v2). Option 1 is run with v2 as well
on both sets, under its own rule: adopt v2 if its recall@25 beats v1's by ≥ 3 points on **both** sets. The current
check set has been seen by v2, so it is not reused.

## 6. Plan changes if approved

- **§1.2:** "dense retrieval over our own corpus" stays a non-goal unless this comparison adopts the hybrid.
- **§1.3:** `retmax_candidates` may exceed 200 in evaluation; the production value is whatever D3 picks.
- **§11.5 D6:** the bucket-test gate is replaced by this comparison. **D3** gains the 500 and 1,000 options.
- **§5.4c:** a two-stage rerank is allowed if the cross-encoder alone is too slow.
- Plan version bump to v1.6, and the §3 diagram updated only if the hybrid is adopted.

## 7. Work and cost

Order: P2 acceptance → P3 cross-encoder (needed by all options) → the steps below, alongside the extractor work.

| Step | What | Effort |
|---|---|---|
| B0 | Verify the embedding chunks: `.npy` header (dtype, shape), PMID alignment, vector norms, encoder version, license | minutes |
| B1 | Build the IVF-PQ index once: stream 38 chunks (~103 GB) into a ~2.5-3 GB index with PMIDs as ids, on the laptop or a temporary cloud VM | ~half a day, mostly download time |
| B2 | `retrieval/dense.py` (MedCPT query encoder + FAISS search), merge step with `retrieved_by: dense / boolean`, two-stage rerank if needed, eval support for the three options | ~1 day |
| B3 | Run both sets, all options, record in `docs/MODEL_EVAL.md` | a few hours, mostly NCBI + rerank time |

- **LLM cost:** about 1-3 calls per question (Query Builder only), the same for every option, since all cut to 25
  before extraction. Roughly 100-300 calls for both sets, cached afterwards.
- **New dependencies:** `faiss-cpu` (pinned, at least 2 weeks old). `torch` and `transformers` come with P3 anyway.
- **Nothing hosted:** the index is a local file. A cloud VM, if used, exists only for the build.

## 8. Risks

| Risk | Handling |
|---|---|
| MedCPT saw BioASQ data | Recent set, rule (b) |
| Embedding dump stops early 2024 | Report "relevant PMIDs in the index"; Boolean covers newer papers in the hybrid |
| IVF-PQ loses recall | Compare `nprobe` 16 / 32 / 64 on a few questions; report the setting used |
| BioASQ questions are narrower than real runs | Golden-question anchor recall reported alongside |
| Rerank too slow on the laptop | Two-stage rerank, the same for all options; rule (c) |
| Fitting to the comparison sets | Rule fixed here, sets drawn before the code exists, one run per option, no tuning between runs |
