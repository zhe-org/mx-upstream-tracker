"""Analyzer agent (Milestone 5).

Only runs when preflight flags something. Consumes the focused handoff bundle
(:class:`~tracker.models.EvidenceBundle`, not the full raw diff) and produces the
deep analysis:
  - CVEs: what, severity, affected component/code path in our fork
  - merge conflicts: which files, why, resolution hints
  - behaviour changes / deprecations: what changed, blast radius, verify what
  - concrete reviewer guidance

The agent's project knowledge (mixed sources, the Canonical branching model,
Superdistro dependency policy, the "CI/workflow conflicts are trivial" rule) is
kept out of the code in ``tracker/prompts/analyzer_system.txt`` and loaded at
runtime via :func:`system_prompt`, so it can be tuned without code changes.

The LLM node body lands in a follow-up; for now :func:`analyzer_node` is a
passthrough (preflight already emits a complete, analysis-free flagged finding).
"""

from __future__ import annotations

from tracker.models import SubgraphState
from tracker.prompts import load_prompt

_SYSTEM_PROMPT_NAME = "analyzer_system"


def system_prompt() -> str:
    """Load the analyzer's system prompt (project knowledge) at runtime."""
    return load_prompt(_SYSTEM_PROMPT_NAME)


def analyzer_node(state: SubgraphState) -> SubgraphState:
    """Deep-dive on a flagged tag and enrich its ``analysis`` block.

    Passthrough for now: preflight already produced a complete (analysis-free)
    flagged finding plus the evidence bundle in ``state['evidence']``. The real
    LLM analysis (using :func:`system_prompt` + the evidence bundle) lands in a
    follow-up; today this leaves the finding unchanged.
    """
    return {}
