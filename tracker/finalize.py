"""End-of-run finalize step.

Runs once, after a run that found new tags, and persists its outcome:

  1. Merges the (stamped) findings into the release set's 7-day report
     (``reports/report-<set>.json``) and drops tags older than the window.
  2. Advances the per-repo watermark in ``state/processed-<set>.json`` to the
     newest tag reported, so the next run does not report those tags again.

The baseline is never written back: it lives in each fork branch's
``canonical/upstream-version``. Keeping this out of the graph (a post-run step)
means we never persist anything for a run that failed part-way.
"""

from __future__ import annotations

from datetime import datetime

from tracker.config import Settings
from tracker.models import Finding
from tracker.reports import SetReport, load_report, merge, prune, save_report
from tracker.state import load_scanned, save_scanned
from tracker.versioning import newest_tag


def finalize_run(
    findings: list[Finding], settings: Settings, release_set: str, now: datetime
) -> None:
    """Persist this run's findings into the report store + advance the watermark."""

    reported = {f.repo.name: [nt.tag for nt in f.new_tags] for f in findings if f.new_tags}
    if not reported:
        return  # quiet run: nothing reported, nothing to persist.

    stored = load_report(settings.reports_path)
    kept = prune(merge(stored.findings if stored else [], findings), now)
    save_report(
        settings.reports_path,
        SetReport(release_set, now.isoformat(timespec="seconds"), kept),
    )

    scanned = load_scanned(settings.state_path)
    for name, tags in reported.items():
        scanned[name] = newest_tag(tags)
    save_scanned(scanned, settings.state_path)
