"""Preflight check agent (Milestone 4).

Runs on every new tag; the gate for the whole sub-graph. Always performs:
  - fetch release notes / docs diff
  - inspect tag metadata + tag-to-tag commit log vs our last merged tag
  - CVE scan (release notes, commit messages, dependency manifest changes)
  - trial merge in a throwaway temp workspace on the runner (capture output; may fail)

Then decides ``clean`` (emit short all-clear finding) or ``flagged`` (build a
handoff bundle of flagged items + evidence for the analyzer).

Skeleton; node logic lands in Milestone 4.
"""

from __future__ import annotations

from tracker.models import GraphState


def preflight_node(state: GraphState) -> GraphState:
    """Run cheap checks + trial merge and set a clean/flagged decision."""
    raise NotImplementedError("Milestone 4: preflight checks + trial merge + decision")


def route_after_preflight(state: GraphState) -> str:
    """Conditional edge: 'analyzer' when flagged, else 'assemble'."""
    raise NotImplementedError("Milestone 4: routing on preflight decision")
