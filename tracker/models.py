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
    - ``current_upstream_tag`` — the newest upstream tag our fork currently
      sits on; the baseline the orchestrator diffs new upstream tags against.
      The tracked line is implied by this tag (e.g. ``v1.14.6`` ⇒ we track
      ``v1.14.*``). Since v1 does no real merge, the finalize step bumps this
      to the newest reported tag at the end of a run (assuming it will be
      merged before the next upstream tag lands), so the next run starts from
      the advanced baseline.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    upstream: str
    canonical_repo: str
    canonical_branch: str
    current_upstream_tag: str


class Finding(BaseModel):
    """Structured, per-repo finding consumed by the reporter (Milestone 3).

    Holds the whole :class:`RepoConfig` as ``repo`` rather than flattening a few
    fields out of it. Downstream steps need the full config: the preflight
    trial merge uses ``canonical_repo`` / ``canonical_branch`` / ``upstream``,
    and the reporter derives ``tracked_version`` and ``last_merged_tag`` from
    ``current_upstream_tag``. Per-tag structured findings and the
    ``preflight`` / ``analysis`` blocks land in M3+.
    """

    repo: RepoConfig
    # Per-tag findings; enriched (risk/summary/preflight/...) from M3 onward.
    new_tags: list[dict] = Field(default_factory=list)


class RepoJob(BaseModel):
    """Focused per-repo unit of work handed to a sub-graph (Milestone 2).

    The orchestrator builds one ``RepoJob`` for each repo that has at least one
    new qualifying upstream tag. It carries only the minimal context a sub-graph
    needs: the repo config and the list of new tags (oldest -> newest).
    """

    repo: RepoConfig
    new_tags: list[str]


class GraphState(TypedDict, total=False):
    """State passed through the LangGraph orchestrator + sub-graphs."""

    repos: list[RepoConfig]
    jobs: list[RepoJob]
    findings: list[Finding]
    report_markdown: str
    report_json: str
    tldr: str
