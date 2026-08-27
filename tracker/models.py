"""Data models shared across the graph.

The :class:`Finding` schema is the contract between each repo sub-graph and the
reporter (spec: "Assembled finding"). Everything except ``repo`` lives per-tag,
inside each :class:`NewTagFinding`: one repo can jump several tags and each is
judged on its own. The reporter consumes only these structured findings, never
raw diffs or merge logs.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, computed_field

from tracker.versioning import line_label

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


class TrialMerge(BaseModel):
    """Outcome of the preflight trial merge (M4).

    Captured from merging the new upstream tag into a throwaway clone of the
    fork branch on the runner. The merge need not succeed — ``result`` records
    what happened and ``output_ref`` links the stashed merge log artifact.
    """

    environment: str = "runner-tmp-workspace"
    result: MergeResult
    conflicting_paths: list[str] = Field(default_factory=list)
    output_ref: str | None = None


class Preflight(BaseModel):
    """Evidence + decision from the preflight gate; always present (M4)."""

    docs_reviewed: bool = False
    cve_refs_found: bool = False
    trial_merge: TrialMerge
    decision: Decision


class Highlight(BaseModel):
    """A notable change worth surfacing (behaviour change, deprecation, CVE fix).

    ``kind`` is free-form (common values: ``behaviour_change``, ``deprecation``,
    ``cve_fix``, ``api_change``); the discriminating fields (``area`` vs
    ``component``/``cve``/``severity``) are optional so one model covers all
    kinds.
    """

    kind: str
    detail: str
    area: str | None = None
    component: str | None = None
    cve: str | None = None
    severity: str | None = None
    upstream_ref: str | None = None


class Dependency(BaseModel):
    """A dependency bump, classified by ``reason`` (e.g. cve_fix vs routine_bump).

    ``from`` is a Python keyword, so the field is ``from_`` with a ``from`` alias;
    dump with ``by_alias=True`` to emit the spec's ``from``/``to`` keys.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    from_: str = Field(alias="from")
    to: str
    reason: str
    cve: str | None = None


class Cve(BaseModel):
    """Per-CVE deep analysis (analyzer, M5)."""

    cve: str
    severity: str
    affects: str
    detail: str


class Conflict(BaseModel):
    """Per-conflict deep analysis with a resolution hint (analyzer, M5)."""

    path: str
    cause: str
    resolution_hint: str


class Analysis(BaseModel):
    """Deep-dive block; present only when preflight flagged the tag (M5)."""

    cves: list[Cve] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)


class EvidenceBundle(BaseModel):
    """Handoff bundle preflight builds for the analyzer (spec: "handoff bundle").

    Carries the evidence the analyzer reasons over — release notes, the
    tag-to-tag commit messages, detected CVE references, and the trial merge's
    captured output + conflict hunks. Only assembled when preflight flags a tag;
    the analyzer never re-fetches from GitHub.
    """

    release_notes: str = ""
    commit_messages: list[str] = Field(default_factory=list)
    cve_refs: list[str] = Field(default_factory=list)
    trial_merge_output: str = ""
    conflict_hunks: str = ""


class AnalyzerOutput(BaseModel):
    """Structured LLM result the analyzer merges into a flagged finding (M5).

    Reuses the finding's own sub-models so the analyzer's output maps straight
    onto ``highlights`` / ``dependencies`` / ``analysis`` / ``notes_for_reviewer``.
    ``risk`` is the analyzer's assessment; the node takes the max of it and the
    preflight risk (the analyzer may escalate but never downgrade).
    """

    risk: Risk = "medium"
    highlights: list[Highlight] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    cves: list[Cve] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    notes_for_reviewer: str | None = None


class NewTagFinding(BaseModel):
    """Everything the reporter needs about one new upstream tag.

    Clean releases are short: ``preflight.decision == "clean"``,
    ``trial_merge.result == "clean"``, no ``analysis`` block, one-line summary.
    Flagged releases add ``highlights`` / ``dependencies`` / ``analysis``.
    """

    tag: str
    risk: Risk
    summary: str
    preflight: Preflight
    highlights: list[Highlight] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    analysis: Analysis | None = None
    notes_for_reviewer: str | None = None


class Finding(BaseModel):
    """Structured, per-repo finding consumed by the reporter (Milestone 3).

    Holds the whole :class:`RepoConfig` as ``repo`` rather than flattening a few
    fields out of it: the preflight trial merge needs ``canonical_repo`` /
    ``canonical_branch`` / ``upstream``. ``tracked_version`` and
    ``last_merged_tag`` are *computed* from ``repo`` (so they still appear in the
    serialized output the reporter emits, without duplicating data that can
    drift).
    """

    repo: RepoConfig
    new_tags: list[NewTagFinding] = Field(default_factory=list)

    @computed_field
    @property
    def tracked_version(self) -> str:
        return line_label(self.repo.current_upstream_tag)

    @computed_field
    @property
    def last_merged_tag(self) -> str:
        return self.repo.current_upstream_tag


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


class SubgraphState(TypedDict, total=False):
    """State threaded through the per-repo sub-graph, one tag at a time (M4).

    The sub-graph is invoked once per new tag: ``preflight_node`` reads
    ``repo`` + ``tag`` and writes the assembled ``tag_finding``; the analyzer
    (M5) enriches it in place when preflight flagged the tag.
    """

    repo: RepoConfig
    tag: str
    tag_finding: NewTagFinding
    evidence: EvidenceBundle
