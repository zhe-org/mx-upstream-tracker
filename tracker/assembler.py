"""Finding assembler (Milestone 3).

Thin factory that turns per-tag results into the one-finding-per-repo contract
the reporter consumes. It stays deliberately small: the judgement lives in the
preflight/analyzer agents (M4/M5); this module just packages their output and
enforces the invariants (one finding per repo, deterministic tag order, clean
findings are short).
"""

from __future__ import annotations

from collections.abc import Iterable

from tracker.models import (
    Dependency,
    Finding,
    Highlight,
    NewTagFinding,
    Preflight,
    RepoConfig,
    Risk,
    TrialMerge,
)
from tracker.versioning import tag_sort_key


def assemble_finding(repo: RepoConfig, new_tags: Iterable[NewTagFinding]) -> Finding:
    """Build one :class:`Finding` for ``repo`` from its per-tag findings.

    Sorts tags oldest -> newest so the assembled finding is deterministic
    regardless of the order the sub-graph produced them in.
    """

    ordered = sorted(new_tags, key=lambda entry: tag_sort_key(entry.tag))
    return Finding(repo=repo, new_tags=ordered)


def clean_tag_finding(
    tag: str,
    *,
    summary: str,
    risk: Risk = "low",
    docs_reviewed: bool = True,
    cve_refs_found: bool = False,
    trial_merge: TrialMerge | None = None,
    highlights: list[Highlight] | None = None,
    dependencies: list[Dependency] | None = None,
) -> NewTagFinding:
    """A clean-path entry: ``decision == clean`` and **no** ``analysis`` block.

    Encodes the "clean releases are short" invariant so callers can't
    accidentally attach a deep-dive analysis to a clean tag.
    """

    return NewTagFinding(
        tag=tag,
        risk=risk,
        summary=summary,
        preflight=Preflight(
            docs_reviewed=docs_reviewed,
            cve_refs_found=cve_refs_found,
            trial_merge=trial_merge or TrialMerge(result="clean"),
            decision="clean",
        ),
        highlights=highlights or [],
        dependencies=dependencies or [],
        analysis=None,
    )


def pending_tag_finding(tag: str) -> NewTagFinding:
    """Placeholder entry for a discovered-but-not-yet-analyzed tag.

    Used by the M2 dispatch shell until the preflight/analyzer nodes (M4/M5)
    produce real findings. Valid against the schema and clearly labelled.
    """

    return clean_tag_finding(tag, summary="Pending preflight analysis (Milestone 4).")


def flagged_preflight_finding(
    tag: str,
    *,
    risk: Risk,
    summary: str,
    trial_merge: TrialMerge,
    docs_reviewed: bool = True,
    cve_refs_found: bool = False,
    highlights: list[Highlight] | None = None,
    dependencies: list[Dependency] | None = None,
    notes_for_reviewer: str | None = None,
) -> NewTagFinding:
    """A flagged-path entry from the preflight gate (M4/M5): ``decision == flagged``.

    Deep analysis (``analysis``) is left ``None`` — that is the analyzer's job
    (M5). ``cve_refs_found`` reflects whether preflight detected any CVE
    references while building the handoff bundle (M5); authoritative CVE
    enrichment is M8.
    """

    return NewTagFinding(
        tag=tag,
        risk=risk,
        summary=summary,
        preflight=Preflight(
            docs_reviewed=docs_reviewed,
            cve_refs_found=cve_refs_found,
            trial_merge=trial_merge,
            decision="flagged",
        ),
        highlights=highlights or [],
        dependencies=dependencies or [],
        analysis=None,
        notes_for_reviewer=notes_for_reviewer,
    )
