"""End-of-run finalize step (Milestone 2).

Runs once, after the whole job has finished successfully. It:

  1. Writes the processed-tag **snapshot** (``state/processed-<set>.json``) — a
     record of what this run reported.
  2. Advances ``current_upstream_tag`` in the selected release-set registry
     (``registries/upstream-tracker-<set>.yaml``) to the newest reported tag per
     repo, so the next nightly run starts from the advanced baseline (encoding
     the "we'll merge it before the next upstream tag lands" assumption).

Keeping this out of the graph (a post-run step) means that in M7 it naturally
becomes "finalize only after successful delivery": we never advance the pointer
for a report that failed to go out. A nightly job (M7) must commit the updated
registry + state snapshot back to the repo.
"""

from __future__ import annotations

from tracker.config import Settings
from tracker.models import Finding, RepoConfig
from tracker.registry import save_registry
from tracker.state import save_processed
from tracker.versioning import newest_tag


def _reported_tags(finding: Finding) -> list[str]:
    return [entry.tag for entry in finding.new_tags]


def finalize_run(
    repos: list[RepoConfig],
    findings: list[Finding],
    settings: Settings,
) -> None:
    """Persist the run's outcome: snapshot processed tags + bump the registry."""

    # repo name -> tags reported this run (union across duplicate-name entries).
    snapshot: dict[str, list[str]] = {}
    # (name, old current_upstream_tag) -> newest reported tag, to target the
    # exact registry entry even when repo names repeat.
    newest_by_entry: dict[tuple[str, str], str] = {}

    for finding in findings:
        tags = _reported_tags(finding)
        if not tags:
            continue
        name = finding.repo.name
        snapshot.setdefault(name, [])
        snapshot[name].extend(tags)
        newest_by_entry[(name, finding.repo.current_upstream_tag)] = newest_tag(tags)

    if not snapshot:
        return  # quiet run: nothing reported, nothing to persist.

    save_processed(snapshot, settings.state_path)

    updated: list[RepoConfig] = []
    for repo in repos:
        key = (repo.name, repo.current_upstream_tag)
        if key in newest_by_entry:
            repo = repo.model_copy(update={"current_upstream_tag": newest_by_entry[key]})
        updated.append(repo)
    save_registry(settings.tracker_path, updated)
