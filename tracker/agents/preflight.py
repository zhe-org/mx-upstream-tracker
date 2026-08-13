"""Preflight check agent (Milestone 4).

The gate for the per-repo sub-graph, run once per new tag. In v1 (CVEs and
behaviour-change judgement deferred to M5/M8) the decision is **deterministic
and merge-driven**: gather evidence, trial-merge the new tag into a throwaway
clone of the fork branch, then decide.

  - trial merge ``clean``    -> ``clean``   (risk low)  — a short all-clear finding
  - trial merge ``conflict`` -> ``flagged`` (risk medium) — needs a manual merge
  - trial merge ``error``    -> ``flagged`` (risk high)  — could not be evaluated

Release notes and the tag-to-tag compare are fetched as evidence (they feed the
summary and, from M5, the analyzer), but they do not drive the M4 decision.
"""

from __future__ import annotations

from tracker.assembler import clean_tag_finding, flagged_preflight_finding
from tracker.config import load_settings
from tracker.models import NewTagFinding, RepoConfig, Risk, SubgraphState, TrialMerge
from tracker.tools.git_ops import trial_merge_fork
from tracker.tools.github import compare_tags, get_release_notes

# Trial-merge result -> risk for the flagged path (clean is always low).
_FLAGGED_RISK: dict[str, Risk] = {"conflict": "medium", "error": "high"}


def _summary(result: str, compare: dict, conflicts: list[str], base: str) -> str:
    if result == "clean":
        commits = compare.get("total_commits", 0)
        files = len(compare.get("files", []))
        return f"Trial merge clean; {commits} commit(s), {files} file(s) changed since {base}."
    if result == "conflict":
        return f"Trial merge conflicts in {len(conflicts)} file(s); needs a manual merge."
    return "Trial merge could not be evaluated (git error)."


def assess_tag(repo: RepoConfig, tag: str, *, settings=None) -> NewTagFinding:
    """Run the deterministic preflight checks for one tag and assemble a finding."""

    settings = settings or load_settings()

    # Evidence (best-effort; does not drive the M4 decision).
    notes = get_release_notes(repo.name, tag)
    compare = compare_tags(repo.name, repo.current_upstream_tag, tag)

    merge = trial_merge_fork(repo, tag, artifact_dir=settings.artifact_dir, token=settings.gh_token)
    trial_merge = TrialMerge(
        result=merge["result"],
        conflicting_paths=merge.get("conflicting_paths", []),
        output_ref=merge.get("output_ref"),
    )

    summary = _summary(
        merge["result"], compare, trial_merge.conflicting_paths, repo.current_upstream_tag
    )
    docs_reviewed = bool(notes)

    if merge["result"] == "clean":
        return clean_tag_finding(
            tag,
            summary=summary,
            risk="low",
            docs_reviewed=docs_reviewed,
            trial_merge=trial_merge,
        )

    return flagged_preflight_finding(
        tag,
        risk=_FLAGGED_RISK[merge["result"]],
        summary=summary,
        trial_merge=trial_merge,
        docs_reviewed=docs_reviewed,
    )


def preflight_node(state: SubgraphState) -> SubgraphState:
    """Run the preflight checks for ``state['tag']`` and store the finding."""
    return {"tag_finding": assess_tag(state["repo"], state["tag"])}


def route_after_preflight(state: SubgraphState) -> str:
    """Conditional edge: 'analyzer' when flagged, else 'assemble'."""
    return "analyzer" if state["tag_finding"].preflight.decision == "flagged" else "assemble"
