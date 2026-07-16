"""Orchestrator agent (Milestone 2).

Cheap, deterministic bookkeeping + dispatch. Responsibilities:
  - load the upstream tracker registry
  - discover new upstream tags matching each repo's constraint
  - dedupe against processed-tag state so we never double-report
  - spawn one sub-graph per repo that has new qualifying tags
  - collect assembled findings and hand them to the reporter

This is a skeleton; node logic lands in Milestone 2.
"""

from __future__ import annotations

from tracker.models import GraphState


def load_tracker_node(state: GraphState) -> GraphState:
    """Load and validate the upstream tracker registry into ``state['repos']``."""
    raise NotImplementedError("Milestone 2: load + validate tracker registry")


def discover_releases_node(state: GraphState) -> GraphState:
    """Attach new, unprocessed, constraint-matching tags per repo."""
    raise NotImplementedError("Milestone 2: upstream release discovery + dedupe")


def dispatch_node(state: GraphState) -> GraphState:
    """Fan out one repo sub-graph per repo that has new tags."""
    raise NotImplementedError("Milestone 2: dispatch sub-graphs")
