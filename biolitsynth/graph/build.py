"""Compile the main graph (plan §8.1). Edges and routing only; behaviour lives in nodes.py / routers.py.

START → plan → dispatch_queries ⇒ query_builder ⇒ approve_queries → dispatch_fetch ⇒ fetch ⇒ dedup
      → rerank ⇒ extract ⇒ quote_check → coverage ─┬─ gap & rounds left ─→ dispatch_queries
                                                   └─ else → cluster_studies → normalize_terms → conflicts
      → synthesize → cite → det_checks ─┬─ critique ─┐
                                        └────────────┴→ route ─┬─ failed & attempts left → synthesize
                                                              └─ else → assemble → END
(⇒ = parallel Send fan-out. `route` is a real node so every conditional edge points at a node.)
"""
from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from . import routers as r
from .nodes import Deps, make_nodes
from .state import RunState


def build_graph(deps: Deps, checkpointer=None):
    lim = deps.settings.limits
    g = StateGraph(RunState)
    for name, fn in make_nodes(deps).items():
        g.add_node(name, fn)

    g.add_edge(START, "plan")
    g.add_edge("plan", "dispatch_queries")
    g.add_conditional_edges("dispatch_queries", r.fan_out_queries, ["query_builder", "approve_queries"])
    g.add_edge("query_builder", "approve_queries")
    g.add_edge("approve_queries", "dispatch_fetch")
    g.add_conditional_edges("dispatch_fetch", r.fan_out_fetch, ["fetch", "dedup"])
    g.add_edge("fetch", "dedup")
    g.add_edge("dedup", "rerank")
    g.add_conditional_edges("rerank", partial(r.fan_out_extract, batch_size=lim.abstracts_per_extraction),
                            ["extract", "quote_check"])
    g.add_edge("extract", "quote_check")
    g.add_edge("quote_check", "coverage")
    g.add_conditional_edges("coverage", partial(r.route_after_coverage, max_extra_rounds=lim.coverage_extra_rounds),
                            ["dispatch_queries", "cluster_studies"])
    g.add_edge("cluster_studies", "normalize_terms")
    g.add_edge("normalize_terms", "conflicts")
    g.add_edge("conflicts", "synthesize")
    g.add_edge("synthesize", "cite")
    g.add_edge("cite", "det_checks")
    g.add_conditional_edges("det_checks", r.route_after_checks, ["critique", "route"])
    g.add_edge("critique", "route")
    g.add_conditional_edges("route", partial(r.route_after_critic, max_retries=lim.synth_retries),
                            ["synthesize", "assemble"])
    g.add_edge("assemble", END)
    return g.compile(checkpointer=checkpointer)
