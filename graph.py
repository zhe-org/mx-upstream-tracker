"""LangGraph assembly.

Wires the top-level orchestrator graph and the per-repo sub-graph
(preflight -> [analyzer if flagged] -> assemble). This module defines the
skeleton structure; individual node implementations arrive with their
milestones. ``build_graph`` returns a compiled graph ready to ``invoke``.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.analyzer import analyzer_node
from agents.orchestrator import (
    discover_releases_node,
    dispatch_node,
    load_registry_node,
)
from agents.preflight import preflight_node, route_after_preflight
from agents.reporter import reporter_node
from models import GraphState


def build_repo_subgraph():
    """Per-repo sub-graph: preflight gates the (conditional) analyzer."""
    sub = StateGraph(GraphState)
    sub.add_node("preflight", preflight_node)
    sub.add_node("analyzer", analyzer_node)

    sub.add_edge(START, "preflight")
    sub.add_conditional_edges(
        "preflight",
        route_after_preflight,
        {"analyzer": "analyzer", "assemble": END},
    )
    sub.add_edge("analyzer", END)
    return sub.compile()


def build_graph():
    """Top-level orchestrator graph: discover -> dispatch -> report."""
    graph = StateGraph(GraphState)
    graph.add_node("load_registry", load_registry_node)
    graph.add_node("discover_releases", discover_releases_node)
    graph.add_node("dispatch", dispatch_node)
    graph.add_node("reporter", reporter_node)

    graph.add_edge(START, "load_registry")
    graph.add_edge("load_registry", "discover_releases")
    graph.add_edge("discover_releases", "dispatch")
    graph.add_edge("dispatch", "reporter")
    graph.add_edge("reporter", END)
    return graph.compile()
