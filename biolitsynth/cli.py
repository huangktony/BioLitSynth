"""CLI. P0 only supports `run --dry`; live runs arrive with P2+."""
from __future__ import annotations

import argparse
import asyncio
import sys

from .config import load_settings
from .graph.runner import run_dry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="biolitsynth")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run the pipeline on a research question")
    run.add_argument("question")
    run.add_argument("--dry", action="store_true",
                     help="walk the whole graph with stubs, forcing every bounded loop once; no network, no LLM")
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
        print("Live runs are not implemented yet (P2 onward). Use --dry.", file=sys.stderr)
        return 2

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
