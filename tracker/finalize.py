"""End-of-run finalize step.

Runs once, after the whole job has finished successfully, and advances the
per-repo watermark in ``state/processed-<set>.json`` to the newest tag reported
this run, so the next run does not report those tags again. The baseline is
never written back: it lives in each fork branch's ``canonical/upstream-version``.

Keeping this out of the graph (a post-run step) means we never advance the
watermark for a run that failed part-way.
"""

from __future__ import annotations

from tracker.config import Settings
from tracker.models import Finding
from tracker.state import load_scanned, save_scanned
from tracker.versioning import newest_tag


def finalize_run(findings: list[Finding], settings: Settings) -> None:
    """Advance the last-scanned watermark for every repo reported this run."""

    reported = {f.repo.name: [nt.tag for nt in f.new_tags] for f in findings if f.new_tags}
    if not reported:
        return  # quiet run: nothing reported, nothing to persist.

    scanned = load_scanned(settings.state_path)
    for name, tags in reported.items():
        scanned[name] = newest_tag(tags)
    save_scanned(scanned, settings.state_path)
