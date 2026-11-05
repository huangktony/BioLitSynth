"""CLI. `run --dry` walks the whole graph on stubs. A live `run` covers what is real so far (P2): Lead plan (or
hand-given sub-questions) → LLM Query Builder refined on esearch counts → fetch → dedup, then it stops."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .config import load_settings
from .graph.runner import LIVE_STOP_AFTER, run_dry, run_live


def main(argv: list[str] | None = None) -> int:
    from .env import load_env_file
    load_env_file()                      # ./.env, gitignored; already-exported variables win
    ap = argparse.ArgumentParser(prog="biolitsynth")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run the pipeline on a research question")
    run.add_argument("question")
    run.add_argument("--dry", action="store_true",
                     help="walk the whole graph with stubs, forcing every bounded loop once; no network, no LLM")
    run.add_argument("--sub-question", action="append", default=[], metavar="TEXT[ | CONCEPT; CONCEPT]",
                     help="live runs only: skip the Lead and use these 2 to 4 sub-questions. Optional key "
                          "concepts after '|', separated by ';'")
    run.add_argument("--config", default=None, help="path to config.yaml")
    fetch = sub.add_parser("fetch", help="P1 demo: fetch, store and dedup one hand-written PubMed query")
    fetch.add_argument("query", help='PubMed syntax, e.g. "metformin AND colorectal neoplasms[mh]"')
    fetch.add_argument("--retmax", type=int, default=25)
    fetch.add_argument("--config", default=None, help="path to config.yaml")
    ev = sub.add_parser("eval", help="evaluation (plan §11.3b, §11.5)")
    evs = ev.add_subparsers(dest="eval_cmd", required=True)
    sel = evs.add_parser("select", help="draw the BioASQ subset by the §11.3b rules (a person reviews it after)")
    sel.add_argument("--bioasq", required=True, help="BioASQ Task B training JSON (keep it under data/eval/bioasq/)")
    sel.add_argument("--out", default="tests/golden/bioasq_subset.yaml")
    sel.add_argument("--seed", type=int, default=0)
    ret = evs.add_parser("retrieval", help="retrieval-only eval on a BioASQ split: recall@fetch, draft vs refined")
    ret.add_argument("--bioasq", required=True)
    ret.add_argument("--subset", default="tests/golden/bioasq_subset.yaml")
    ret.add_argument("--split", choices=["dev", "held_back"], default="dev")
    ret.add_argument("--limit", type=int, default=None, help="only the first N questions of the split")
    ret.add_argument("--max-llm-calls", type=int, default=200)
    ret.add_argument("--config", default=None, help="path to config.yaml")
    args = ap.parse_args(argv)

    if args.cmd == "eval":
        return _eval(args)

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
        if args.sub_question and not 2 <= len(args.sub_question) <= 4:
            print("Give 2 to 4 --sub-question values, or none to let the Lead plan.", file=sys.stderr)
            return 2
        from .llm import MissingApiKey, ModelNotVerified
        try:
            lv = asyncio.run(run_live(args.question, args.sub_question or None, load_settings(args.config)))
        except (MissingApiKey, ModelNotVerified) as e:
            print(f"cannot start: {e}", file=sys.stderr)
            return 2
        print(f"run_id        {lv.run_id}")
        print(f"run dir       {lv.run_dir}")
        print(f"plan          {lv.plan.effort}, {len(lv.plan.sub_questions)} sub-questions"
              f"{' (given by hand)' if args.sub_question else ' (Lead)'}")
        for sq in lv.plan.sub_questions:
            print(f"  {sq.id} [{sq.facet.value}] {sq.text}")
            print(f"      concepts: {'; '.join(sq.key_concepts) or '-'}")
        for b in lv.plan.bridges:
            print(f"  {b.id} {b.from_sub_question_id}->{b.to_sub_question_id}: {b.hypothesis}")
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


def _eval(args) -> int:
    from .eval.bioasq import load_bioasq, load_subset, select_subset, write_subset
    questions = load_bioasq(args.bioasq)
    if args.eval_cmd == "select":
        subset = select_subset(questions, seed=args.seed)
        write_subset(subset, args.bioasq, args.out)
        print(f"wrote {args.out}: {len(subset['dev'])} dev, {len(subset['held_back'])} held-back question ids")
        print("A person reviews the list and sets reviewed_by before any tuning.")
        return 0

    from .eval.retrieval import run_retrieval_eval
    from .graph.runner import resolve_clients
    from .llm import MissingApiKey, ModelNotVerified
    subset = load_subset(args.subset)
    if args.split == "held_back":
        print("Note: the held-back split is for the final report only (plan §11.5 rule 2), never for choosing.",
              file=sys.stderr)
    ids = subset[args.split][: args.limit]
    missing = [i for i in ids if i not in questions]
    if missing:
        print(f"{len(missing)} subset ids are not in {args.bioasq}, e.g. {missing[:3]}", file=sys.stderr)
        return 2
    settings = load_settings(args.config)
    try:
        client = resolve_clients(settings, roles=("query",))["query"]
    except (MissingApiKey, ModelNotVerified) as e:
        print(f"cannot start: {e}", file=sys.stderr)
        return 2
    report, path = asyncio.run(run_retrieval_eval([questions[i] for i in ids], settings, split=args.split,
                                                  client=client, max_llm_calls=args.max_llm_calls))
    for k, v in report.summary().items():
        print(f"{k:<26}{v}")
    print(f"{'details':<26}{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
