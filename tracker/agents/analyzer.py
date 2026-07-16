"""Analyzer agent (Milestone 5).

Only runs when preflight flags something. Consumes the focused handoff bundle
(not the full raw diff) and produces the deep analysis:
  - CVEs: what, severity, affected component/code path in our fork
  - merge conflicts: which files, why, resolution hints
  - behaviour changes / deprecations: what changed, blast radius, verify what
  - concrete reviewer guidance

Skeleton; node logic lands in Milestone 5.
"""

from __future__ import annotations

from tracker.models import GraphState


def analyzer_node(state: GraphState) -> GraphState:
    """Produce the ``analysis`` block from the handoff bundle."""
    raise NotImplementedError("Milestone 5: deep analysis of flagged bundle")
