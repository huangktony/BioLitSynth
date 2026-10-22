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
    args = ap.parse_args(argv)

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
