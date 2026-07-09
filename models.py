"""Data models shared across the graph.

The :class:`Finding` schema is the contract between each repo sub-graph and the
reporter (spec: "Assembled finding"). This is a skeleton placeholder fleshed
out in Milestone 3 — only the outer shape is defined here so modules can import
and type against it now.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, Field

Risk = Literal["low", "medium", "high"]
Decision = Literal["clean", "flagged"]
MergeResult = Literal["clean", "conflict", "error"]


class RepoConfig(BaseModel):
    """One entry in the repo registry (Milestone 1)."""

    repo: str
    upstream_url: str
    tracked_version: str
    fork_url: str
    last_merged_tag: str
    reviewers: list[str] = Field(default_factory=list)
    areas_to_watch: list[str] = Field(default_factory=list)


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
