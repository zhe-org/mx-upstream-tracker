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

from tracker.models import SubgraphState


def analyzer_node(state: SubgraphState) -> SubgraphState:
    """Deep-dive on a flagged tag and enrich its ``analysis`` block.

    M4 passthrough: preflight already produced a complete (analysis-free)
    flagged finding, so for now the analyzer is a no-op that leaves the finding
    unchanged. Milestone 5 replaces this body with the real CVE / conflict /
    behaviour-change analysis.
    """
    return {}
