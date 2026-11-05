"""Retrieval eval end to end on a mock NCBI and scripted Query Builder replies. The question and its relevant PMIDs
are synthetic; every expected number follows from the hand-chosen search results below."""
import json

from biolitsynth.eval.bioasq import BioasqQuestion
from biolitsynth.eval.retrieval import run_retrieval_eval
from biolitsynth.pubmed import TokenBucket

from ..llm_mock import ScriptedClient
from ..pubmed_mock import CRC_IDS, FakeNcbi

X1, X2 = "41911553", "39502250"            # relevant but not in the final query's top results
DRAFT, FINAL, INBAND = "metformin[majr] AND crc[ti]", "metformin AND colorectal", "statin AND liver"
PROBE = f"({FINAL}) AND ({X1}[pmid] OR {X2}[pmid])"
Q1 = BioasqQuestion("bq1", "Does metformin lower colorectal cancer risk?", "yesno", (CRC_IDS[0], CRC_IDS[1], X1, X2))
Q2 = BioasqQuestion("bq2", "Do statins protect the liver?", "yesno", (CRC_IDS[0], CRC_IDS[5]))


async def test_recall_draft_vs_refined_and_miss_buckets(settings, tmp_path):
    ncbi = FakeNcbi(searches={DRAFT: [CRC_IDS[0]], FINAL: CRC_IDS, PROBE: [X1], INBAND: CRC_IDS[:3]},
                    totals={DRAFT: 3, FINAL: 150, INBAND: 90})
    client = ScriptedClient([
        ("colorectal cancer risk?\nFacet", {"query_string": DRAFT, "rationale": "r"}),
        ("Broaden it", {"query_string": FINAL, "rationale": "r"}),
        ("protect the liver?", {"query_string": INBAND, "rationale": "r"}),
    ])
    report, path = await run_retrieval_eval([Q1, Q2], settings, split="dev", client=client, transport=ncbi.transport,
                                            ncbi_env={}, llm_limiter=TokenBucket(10_000),
                                            ncbi_limiter=TokenBucket(10_000), out_dir=tmp_path / "out")
    r1, r2 = report.questions
    assert (r1.draft_query, r1.final_query, r1.refinements, r1.in_band) == (DRAFT, FINAL, 1, True)
    assert (r1.recall_fetch, r1.recall_fetch_draft) == (0.5, 0.25)       # 2 of 4 vs 1 of 4
    assert r1.misses == {X1: "beyond_cap", X2: "not_matched"}
    assert (r2.recall_fetch, r2.recall_fetch_draft, r2.refinements, r2.misses) == (0.5, 0.5, 0, {CRC_IDS[5]: "not_matched"})
    s = report.summary()
    assert (s["questions"], s["mean_recall_fetch"], s["mean_recall_fetch_draft"]) == (2, 0.5, 0.375)
    assert (s["share_in_band"], s["misses_beyond_cap"], s["misses_not_matched"], s["llm_calls"]) == (1.0, 1, 2, 3)
    saved = json.loads(path.read_text())
    assert saved["summary"] == s and saved["prompt_version"] == "query_builder.v1"
    assert (tmp_path / "out" / "trace.jsonl").exists()
