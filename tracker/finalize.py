"""End-of-run finalize step.

Runs once, after the whole job has finished successfully, and writes the
processed-tag snapshot (``state/processed-<set>.json``) — a record of what this
run reported. The baseline is never written back: it lives in each fork
branch's ``canonical/upstream-version``.

Keeping this out of the graph (a post-run step) means we never record tags for
a run that failed part-way.
"""

from __future__ import annotations

from tracker.config import Settings
from tracker.models import Finding
from tracker.state import save_processed


def finalize_run(findings: list[Finding], settings: Settings) -> None:
    """Persist the run's outcome: snapshot the tags reported this run."""

    snapshot: dict[str, list[str]] = {}
    for finding in findings:
        tags = [entry.tag for entry in finding.new_tags]
        if tags:
            snapshot.setdefault(finding.repo.name, []).extend(tags)

    if not snapshot:
        return  # quiet run: nothing reported, nothing to persist.

    save_processed(snapshot, settings.state_path)
