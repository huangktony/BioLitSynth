"""CLI. `run --dry` walks the whole graph on stubs. A live `run` covers what is real so far (P2): hand-given
sub-questions → LLM Query Builder refined on esearch counts → fetch → dedup, then it stops."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .config import load_settings
from .graph.runner import LIVE_STOP_AFTER, run_dry, run_live


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="biolitsynth")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run the pipeline on a research question")
    run.add_argument("question")
    run.add_argument("--dry", action="store_true",
                     help="walk the whole graph with stubs, forcing every bounded loop once; no network, no LLM")
    run.add_argument("--sub-question", action="append", default=[], metavar="TEXT[ | CONCEPT; CONCEPT]",
                     help="live runs only, give 2 to 4 (the Lead's plan call is not built yet). Optional key "
                          "concepts after '|', separated by ';'")
    run.add_argument("--config", default=None, help="path to config.yaml")
    fetch = sub.add_parser("fetch", help="P1 demo: fetch, store and dedup one hand-written PubMed query")
    fetch.add_argument("query", help='PubMed syntax, e.g. "metformin AND colorectal neoplasms[mh]"')
    fetch.add_argument("--retmax", type=int, default=25)
    fetch.add_argument("--config", default=None, help="path to config.yaml")
    args = ap.parse_args(argv)

    if args.cmd == "fetch":
        from .pubmed.demo import fetch_demo
        f = asyncio.run(fetch_demo(args.query, load_settings(args.config), retmax=args.retmax))
        print(f"run_id          {f.run_id}")
        print(f"PubMed hits     {f.total_hits} (took top {f.candidates}{', search cached' if f.search_cached else ''})")
        print(f"stored          {f.stored} with abstracts ({f.skipped_no_abstract} skipped: no abstract)")
        print(f"after dedup     {f.kept} kept, dropped {f.dropped or 'none'}")
        print(f"XML from cache  {f.cache_hits}")
        print(f"network calls   {f.network_calls}")
        return 0

    if not args.dry:
        if not 2 <= len(args.sub_question) <= 4:
            print("A live run needs 2 to 4 --sub-question values (the Lead's plan call is not built yet), "
                  "or use --dry.", file=sys.stderr)
            return 2
        from .llm import MissingApiKey, ModelNotVerified
        try:
            lv = asyncio.run(run_live(args.question, args.sub_question, load_settings(args.config)))
        except (MissingApiKey, ModelNotVerified) as e:
            print(f"cannot start: {e}", file=sys.stderr)
            return 2
        print(f"run_id        {lv.run_id}")
        print(f"run dir       {lv.run_dir}")
        for q in lv.queries:
            hist = json.loads(q["refinement_history_json"] or "[]")
            print(f"{q['id']} ({q['sub_question_id']})  {q['hit_count']} hits after {len(hist)} attempt(s)")
            for i, (qs, hits) in enumerate(hist, 1):
                print(f"    {i}. {hits:>8} hits  {qs}")
        print(f"papers        {lv.papers_kept} kept after dedup, {lv.papers_dropped} dropped")
        print(f"LLM calls     {lv.llm_calls} ({lv.llm_cache_hits} more from cache)")
        print(f"NCBI calls    {lv.network_calls}")
        print(f"trace         {lv.trace_path}")
        print(f"stopped after {LIVE_STOP_AFTER} (rerank, extraction and later stages are not built yet)")
        return 0

    s = asyncio.run(run_dry(args.question, load_settings(args.config)))
    st = s.state
    print(f"run_id        {s.run_id}")
    print(f"run dir       {s.run_dir}")
    print(f"trace         {s.trace_path} ({s.n_events} events)")
    print(f"stages        {dict(s.stage_counts)}")
    print(f"loops         gap rounds={st['coverage_rounds']}  synth attempts={st['synth_attempt'] + 1}"
          f"  queries={len(st['query_ids'])}  evidence={len(st['evidence_ids'])}")
    print(f"final status  {st['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
