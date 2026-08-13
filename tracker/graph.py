"""Graph / pipeline assembly.

This module owns everything that touches the compiled per-repo sub-graph:

  - :func:`run_orchestrator` — the deterministic top-level pipeline
    (load → discover → dispatch). Plain Python, not a LangGraph: the
    orchestrator is bookkeeping + dispatch, so a linear function is clearer.
  - :func:`build_repo_subgraph` — the per-repo worker, where LangGraph earns its
    keep (the preflight gate conditionally routes to the analyzer).
  - :func:`run_repo_subgraph` / :func:`dispatch` — fan-out: turn each
    ``RepoJob`` into one ``Finding`` by invoking the sub-graph.

Discovery (``load_tracker_node`` / ``discover_releases_node``) lives in
:mod:`tracker.agents.orchestrator` and has no sub-graph knowledge, so the
dependency flows one way (``graph`` → ``orchestrator``) with no import cycle.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from langgraph.graph import END, START, StateGraph

from tracker.agents.analyzer import analyzer_node
from tracker.agents.orchestrator import discover_releases_node, load_tracker_node
from tracker.agents.preflight import preflight_node, route_after_preflight
from tracker.assembler import assemble_finding
from tracker.config import load_settings
from tracker.models import Finding, GraphState, RepoJob, SubgraphState


def run_orchestrator() -> GraphState:
    """Run the orchestrator pipeline (load → discover → dispatch) and return state."""
    state: GraphState = {}
    state.update(load_tracker_node(state))
    state.update(discover_releases_node(state))
    state.update(dispatch(state))
    return state


def build_repo_subgraph():
    """Per-repo sub-graph (invoked once per tag): preflight gates the analyzer."""
    sub = StateGraph(SubgraphState)
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


# Compile once and reuse across tags/repos (the graph is stateless per invoke).
_REPO_SUBGRAPH = build_repo_subgraph()


def run_repo_subgraph(job: RepoJob) -> Finding:
    """Run the per-repo sub-graph for each of ``job``'s tags and assemble one finding.

    The sub-graph is invoked once per tag (each tag is judged on its own); the
    resulting per-tag findings are assembled into a single repo finding
    (tags sorted oldest → newest by the assembler).
    """

    tag_findings = [
        _REPO_SUBGRAPH.invoke({"repo": job.repo, "tag": tag})["tag_finding"] for tag in job.new_tags
    ]
    return assemble_finding(job.repo, tag_findings)


def _worker_count(n_jobs: int, configured: int | None, cpu: int | None) -> int:
    """Resolve the dispatch pool size.

    Bounded by ``n_jobs`` (never spawn more workers than there is work) and, when
    ``configured`` is unset, by the machine's CPU count. Always at least 1.
    """
    cap = configured if configured and configured > 0 else (cpu or 1)
    return max(1, min(cap, n_jobs))


def dispatch(state: GraphState) -> GraphState:
    """Fan out one sub-graph per job, in parallel, and collect one finding each.

    Repo jobs are I/O-bound (GitHub / git trial merge / LLM), so a thread pool
    gives real concurrency while the GIL is released during those waits. Pool
    size is capped by CPU count (each job's trial merge is heavy) and overridable
    via ``DISPATCH_MAX_WORKERS``. ``ThreadPoolExecutor.map`` preserves input
    order, so findings stay deterministic regardless of completion order.
    """

    jobs = state.get("jobs", [])
    if not jobs:
        return {"findings": []}

    workers = _worker_count(len(jobs), load_settings().dispatch_max_workers, os.cpu_count())
    if workers == 1:
        findings = [run_repo_subgraph(job) for job in jobs]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="repo") as pool:
            findings = list(pool.map(run_repo_subgraph, jobs))
    return {"findings": findings}
