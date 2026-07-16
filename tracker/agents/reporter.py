"""Reporter agent (Milestone 6).

Consumes structured findings only (never raw diffs) and renders:
  - a risk-ranked human-readable Markdown report
  - a machine-readable JSON summary
  - an optional short TL;DR for a chat channel

Skeleton; node logic lands in Milestone 6.
"""

from __future__ import annotations

from tracker.models import GraphState


def reporter_node(state: GraphState) -> GraphState:
    """Render markdown + JSON + TL;DR from ``state['findings']``."""
    raise NotImplementedError("Milestone 6: render report / json / tldr")
