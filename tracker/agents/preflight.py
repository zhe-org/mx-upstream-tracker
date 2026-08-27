"""Preflight check agent (Milestone 4 / 5).

The gate for the per-repo sub-graph, run once per new tag. The decision is
**deterministic and merge-driven**, now also CVE-aware (M5): gather evidence,
trial-merge the new tag into a throwaway clone of the fork branch, then decide.

  - trial merge ``clean`` + no CVE refs -> ``clean``   (risk low) — short all-clear
  - trial merge ``clean`` + CVE ref(s)  -> ``flagged`` (risk medium) — analyzer runs
  - trial merge ``conflict``            -> ``flagged`` (risk medium) — manual merge
  - trial merge ``error``               -> ``flagged`` (risk high)   — could not evaluate

Preflight also assembles the :class:`~tracker.models.EvidenceBundle` handoff for
the analyzer (release notes, tag-to-tag commit log, detected CVE refs, and the
trial merge's captured output + conflict hunks). CVE *enrichment* (authoritative
severity via NVD/Advisory) remains M8; here we only *detect* references.
"""

from __future__ import annotations

from tracker.assembler import clean_tag_finding, flagged_preflight_finding
from tracker.config import load_settings
from tracker.models import (
    EvidenceBundle,
    NewTagFinding,
    RepoConfig,
    Risk,
    SubgraphState,
    TrialMerge,
)
from tracker.tools.cve import find_cve_refs
from tracker.tools.git_ops import trial_merge_fork
from tracker.tools.github import compare_tags, get_release_notes

# Trial-merge result -> risk for the flagged path (clean is low unless a CVE
# reference flags it, in which case it is medium).
_FLAGGED_RISK: dict[str, Risk] = {"conflict": "medium", "error": "high"}
_CVE_ONLY_RISK: Risk = "medium"


def _summary(result: str, commits: int, conflicts: list[str], base: str) -> str:
    if result == "clean":
        return f"Trial merge clean; {commits} commit(s) since {base}."
    if result == "conflict":
        return f"Trial merge conflicts in {len(conflicts)} file(s); needs a manual merge."
    return "Trial merge could not be evaluated (git error)."


def gather_evidence(
    repo: RepoConfig, tag: str, *, settings=None
) -> tuple[EvidenceBundle, TrialMerge]:
    """Collect the handoff evidence + trial-merge outcome for one tag.

    Fetches release notes and the tag-to-tag compare, runs the trial merge, and
    scans notes + commit messages for CVE references. Returns the bundle the
    analyzer consumes and the :class:`TrialMerge` the decision reads.
    """

    settings = settings or load_settings()

    notes = get_release_notes(repo.name, tag)
    commit_messages = compare_tags(repo.name, repo.current_upstream_tag, tag).get(
        "commit_messages", []
    )
    merge = trial_merge_fork(repo, tag, artifact_dir=settings.artifact_dir, token=settings.gh_token)

    cve_refs = find_cve_refs([notes, *commit_messages])

    bundle = EvidenceBundle(
        release_notes=notes,
        commit_messages=commit_messages,
        cve_refs=cve_refs,
        trial_merge_output=merge.get("output", ""),
        conflict_hunks=merge.get("conflict_hunks", ""),
    )
    trial = TrialMerge(
        result=merge["result"],
        conflicting_paths=merge.get("conflicting_paths", []),
        output_ref=merge.get("output_ref"),
    )
    return bundle, trial


def decide(repo: RepoConfig, tag: str, bundle: EvidenceBundle, trial: TrialMerge) -> NewTagFinding:
    """Turn the evidence + trial-merge outcome into a preflight finding.

    Clean merge with no CVE references -> short clean finding; anything else
    (conflict, git error, or a CVE reference) -> flagged so the analyzer runs.
    """

    summary = _summary(
        trial.result,
        len(bundle.commit_messages),
        trial.conflicting_paths,
        repo.current_upstream_tag,
    )
    docs_reviewed = bool(bundle.release_notes)
    cve_found = bool(bundle.cve_refs)

    if trial.result == "clean" and not cve_found:
        return clean_tag_finding(
            tag,
            summary=summary,
            risk="low",
            docs_reviewed=docs_reviewed,
            trial_merge=trial,
        )

    risk = _FLAGGED_RISK.get(trial.result, _CVE_ONLY_RISK)
    return flagged_preflight_finding(
        tag,
        risk=risk,
        summary=summary,
        trial_merge=trial,
        docs_reviewed=docs_reviewed,
        cve_refs_found=cve_found,
    )


def assess_tag(repo: RepoConfig, tag: str, *, settings=None) -> NewTagFinding:
    """Run the deterministic preflight checks for one tag and assemble a finding."""
    bundle, trial = gather_evidence(repo, tag, settings=settings)
    return decide(repo, tag, bundle, trial)


def preflight_node(state: SubgraphState) -> SubgraphState:
    """Run preflight for ``state['tag']``; store the finding + evidence bundle."""
    bundle, trial = gather_evidence(state["repo"], state["tag"])
    finding = decide(state["repo"], state["tag"], bundle, trial)
    return {"tag_finding": finding, "evidence": bundle}


def route_after_preflight(state: SubgraphState) -> str:
    """Conditional edge: 'analyzer' when flagged, else 'assemble'."""
    return "analyzer" if state["tag_finding"].preflight.decision == "flagged" else "assemble"
