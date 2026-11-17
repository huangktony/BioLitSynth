"""CLI. `run --dry` walks the whole graph on stubs. A live `run` covers what is real so far (P3): Lead plan (or
hand-given sub-questions) → LLM Query Builder refined on esearch counts → fetch → dedup → rerank → extract → quote
audit, then it stops."""
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
    sel.add_argument("--n", type=int, default=50)
    sel.add_argument("--dev", type=int, default=30, help="how many of the n go to dev; the rest to held_back")
    sel.add_argument("--exclude", action="append", default=[], metavar="SUBSET_YAML",
                     help="never pick ids already in this subset file (repeatable)")
    sel.add_argument("--created-after", default=None, metavar="YYYY-MM-DD",
                     help="only questions created on or after this date (from the BioASQ id's timestamp)")
    ret = evs.add_parser("retrieval", help="retrieval-only eval on a BioASQ split: recall@fetch, draft vs refined")
    ret.add_argument("--bioasq", required=True)
    ret.add_argument("--subset", default="tests/golden/bioasq_subset.yaml")
    ret.add_argument("--split", choices=["dev", "held_back"], default="dev")
    ret.add_argument("--limit", type=int, default=None, help="only the first N questions of the split")
    ret.add_argument("--max-llm-calls", type=int, default=200)
    ret.add_argument("--prompt-version", type=int, default=None,
                     help="Query Builder prompt version to evaluate (default: config.yaml prompt_versions, else latest)")
    ret.add_argument("--config", default=None, help="path to config.yaml")
    ret.add_argument("--log", default="docs/eval_log.jsonl", help="committed eval log to append to")
    cmp_ = evs.add_parser("compare", help="Track B: Boolean deep vs dense MedCPT vs hybrid, all reranked to 25")
    cmp_.add_argument("--bioasq", required=True)
    cmp_.add_argument("--subset", required=True, help="subset YAML, e.g. tests/golden/bioasq_compare_all.yaml")
    cmp_.add_argument("--split", choices=["dev", "held_back", "all"], default="all",
                      help="all = dev then held_back, in file order")
    cmp_.add_argument("--limit", type=int, default=None, help="only the first N questions")
    cmp_.add_argument("--max-llm-calls", type=int, default=300)
    cmp_.add_argument("--prompt-version", type=int, default=None,
                      help="Query Builder prompt version (default: config.yaml prompt_versions, else latest)")
    cmp_.add_argument("--config", default=None, help="path to config.yaml")
    cmp_.add_argument("--log", default="docs/eval_log.jsonl", help="committed eval log to append to")
    bf = evs.add_parser("backfill-log", help="append completed evals from the store to the eval log if absent")
    bf.add_argument("--config", default=None, help="path to config.yaml")
    bf.add_argument("--log", default="docs/eval_log.jsonl")
    serve = sub.add_parser("serve", help="dashboard over the run store, with a PubMed search box, at http://127.0.0.1:<port>")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--config", default=None, help="path to config.yaml")
    args = ap.parse_args(argv)

    if args.cmd == "eval":
        return _eval(args)

    if args.cmd == "serve":
        import uvicorn
        from .api import create_app
        settings = load_settings(args.config)
        print(f"dashboard on http://127.0.0.1:{args.port}  (store: {settings.paths.data_dir / 'biolitsynth.db'})")
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning")
        return 0

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
        for sq_id, (n, kept) in lv.rerank_by_sub_question.items():
            print(f"rerank {sq_id}  {n:>3} candidates → {kept} kept")
        print(f"rerank model  {lv.rerank_model or '-'}")
        print(f"evidence      {lv.evidence_items} items from {lv.papers_extracted} papers")
        q = lv.quotes
        rate = f"{q.first_try_rate:.1%}" if q.first_try_rate is not None else "n/a"
        print(f"quotes        {q.first_try_ok}/{q.proposed} verbatim on first try ({rate}): {q.exact} exact, "
              f"{q.normalized} normalized, {q.fuzzy} fuzzy; {q.recovered} recovered by retry, {q.dropped} dropped")
        print(f"LLM calls     {lv.llm_calls} ({lv.llm_cache_hits} more from cache)")
        print(f"NCBI calls    {lv.network_calls}")
        print(f"trace         {lv.trace_path}")
        print(f"stopped after {LIVE_STOP_AFTER} (coverage and later stages are not built yet)")
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
    if args.eval_cmd == "backfill-log":
        from .eval.log import backfill
        added = backfill(load_settings(args.config), args.log)
        print(f"appended {len(added)} eval(s) to {args.log}: {added}")
        return 0

    questions = load_bioasq(args.bioasq)
    if args.eval_cmd == "select":
        exclude = {i for f in args.exclude for s in [load_subset(f)] for i in s["dev"] + s["held_back"]}
        from datetime import date
        after = date.fromisoformat(args.created_after) if args.created_after else None
        subset = select_subset(questions, n=args.n, dev=args.dev, seed=args.seed, exclude=exclude, created_after=after)
        write_subset(subset, args.bioasq, args.out)
        print(f"wrote {args.out}: {len(subset['dev'])} dev, {len(subset['held_back'])} held-back question ids")
        print("A person reviews the list and sets reviewed_by before any tuning.")
        return 0

    from .eval.log import append_log, record_from_report, run_config_json
    from .eval.retrieval import run_retrieval_eval
    from .graph.runner import resolve_clients
    from .llm import MissingApiKey, ModelNotVerified
    subset = load_subset(args.subset)
    if args.eval_cmd == "compare":
        return _compare(args, questions, subset)
    if args.split == "held_back":
        print("Note: the held-back split is for the final report only (plan §11.5 rule 2), never for choosing.",
              file=sys.stderr)
    ids = subset[args.split][: args.limit]
    missing = [i for i in ids if i not in questions]
    if missing:
        print(f"{len(missing)} subset ids are not in {args.bioasq}, e.g. {missing[:3]}", file=sys.stderr)
        return 2
    settings = load_settings(args.config)
    if args.prompt_version is not None:
        settings.prompt_versions["query_builder"] = args.prompt_version
    try:
        client = resolve_clients(settings, roles=("query",))["query"]
    except (MissingApiKey, ModelNotVerified) as e:
        print(f"cannot start: {e}", file=sys.stderr)
        return 2
    report, path = asyncio.run(run_retrieval_eval([questions[i] for i in ids], settings, split=args.split,
                                                  client=client, max_llm_calls=args.max_llm_calls))
    append_log(record_from_report(report, settings, config_json=run_config_json(settings, report.eval_id)), args.log)
    for k, v in report.summary().items():
        print(f"{k:<26}{v}")
    print(f"{'details':<26}{path}")
    print(f"{'logged to':<26}{args.log}")
    return 0


def _compare(args, questions, subset) -> int:
    from .eval.compare import OPTIONS, run_compare_eval
    from .eval.log import append_log, record_from_compare_report, run_config_json
    from .graph.runner import resolve_clients
    from .llm import MissingApiKey, ModelNotVerified
    ids = (subset["dev"] + subset["held_back"] if args.split == "all" else subset[args.split])[: args.limit]
    missing = [i for i in ids if i not in questions]
    if missing:
        print(f"{len(missing)} subset ids are not in {args.bioasq}, e.g. {missing[:3]}", file=sys.stderr)
        return 2
    settings = load_settings(args.config)
    if args.prompt_version is not None:
        settings.prompt_versions["query_builder"] = args.prompt_version
    try:
        client = resolve_clients(settings, roles=("query",))["query"]
    except (MissingApiKey, ModelNotVerified) as e:
        print(f"cannot start: {e}", file=sys.stderr)
        return 2
    from .rerank import CrossEncoderReranker
    from .retrieval import DenseIndex
    reranker = CrossEncoderReranker(settings.rerank)
    dense = DenseIndex.from_config(settings.dense)
    report, path = asyncio.run(run_compare_eval(
        [questions[i] for i in ids], settings, split=args.split, client=client, reranker=reranker, dense=dense,
        max_llm_calls=args.max_llm_calls))
    append_log(record_from_compare_report(report, config_json=run_config_json(settings, report.eval_id)), args.log)
    s = report.summary()
    print(f"{'option':<20}{'pool recall':>13}{'recall@25':>11}{'precision@25':>14}")
    for o in OPTIONS:
        r = s["options"][o]
        print(f"{o:<20}{r['pool_recall']:>13}{r['recall_at_25']:>11}{r['precision_at_25']:>14}")
    for k, v in s.items():
        if k != "options":
            print(f"{k:<26}{v}")
    print(f"{'details':<26}{path}")
    print(f"{'logged to':<26}{args.log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
