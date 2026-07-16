"""Data models shared across the graph.

The :class:`Finding` schema is the contract between each repo sub-graph and the
reporter (spec: "Assembled finding"). This is a skeleton placeholder fleshed
out in Milestone 3 — only the outer shape is defined here so modules can import
and type against it now.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

Risk = Literal["low", "medium", "high"]
Decision = Literal["clean", "flagged"]
MergeResult = Literal["clean", "conflict", "error"]


class RepoConfig(BaseModel):
    """One entry in the upstream tracker (Milestone 1).

    Mirrors one row of the tracked-repos table:

    - ``name``            — upstream ``owner/repo`` short id (e.g. ``coredns/coredns``).
    - ``upstream``        — upstream GitHub URL we watch for new tags.
    - ``canonical_repo``  — our Canonical fork (GitHub), e.g.
      ``https://github.com/canonical/mx-coredns``.
    - ``canonical_branch``— the fork's release branch, e.g.
      ``canonical/1.14-26.04/stable``.
    - ``canonical_tag``   — the latest Canonical release tag on that branch.
    - ``last_incorporated_upstream_ref`` — the newest upstream ref already
      merged into our fork (the spec's "last merged tag"; the orchestrator
      diffs new upstream tags against this). The tracked line is implied by
      this ref (e.g. ``v1.14.6`` ⇒ we track ``v1.14.*``).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    upstream: str
    canonical_repo: str
    canonical_branch: str
    canonical_tag: str
    last_incorporated_upstream_ref: str


class Finding(BaseModel):
    """Structured, per-repo finding consumed by the reporter (Milestone 3)."""

    repo: str
    tracked_version: str
    last_merged_tag: str
    # Full sub-schemas (new_tags/preflight/highlights/analysis/...) land in M3.
    new_tags: list[dict] = Field(default_factory=list)


class GraphState(TypedDict, total=False):
    """State passed through the LangGraph orchestrator + sub-graphs."""

    repos: list[RepoConfig]
    findings: list[Finding]
    report_markdown: str
    report_json: str
    tldr: str
