"""Per-release-set report store with a 7-day retention window.

Each release set keeps ``reports/report-<set>.json``: every tag the tracker
reported in the last :data:`RETENTION`, with its analysis. A run adds its new
tags (stamped with ``detected_at`` and the fork ``baseline`` they were compared
against) and drops tags older than the window; the dashboard is built from
these files, so a tag stays visible for a week even though later runs no
longer report it. The same file shape is used for the per-run artifact
(``artifacts/report-<set>.json``, this run's new tags only) that feeds the
Mattermost notification. Shape::

    {
      "release_set": "1.36",
      "generated_at": "2026-10-02T09:00:00+00:00",
      "findings": [ <Finding>, ... ]
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from tracker.models import Finding
from tracker.versioning import tag_sort_key

RETENTION = timedelta(days=7)
REPORT_GLOB = "report-*.json"


@dataclass
class SetReport:
    """One release set's findings + when they were last updated."""

    release_set: str
    generated_at: str | None
    findings: list[Finding] = field(default_factory=list)


# --- lifecycle -----------------------------------------------------------


def stamp(findings: list[Finding], now: datetime) -> list[Finding]:
    """Record when each new tag was detected and the baseline it was diffed from."""

    return [
        finding.model_copy(
            update={
                "new_tags": [
                    nt.model_copy(
                        update={"detected_at": now, "baseline": finding.repo.current_upstream_tag}
                    )
                    for nt in finding.new_tags
                ]
            }
        )
        for finding in findings
    ]


def merge(stored: list[Finding], new: list[Finding]) -> list[Finding]:
    """Add ``new`` tags to ``stored`` (one finding per repo, tags oldest -> newest).

    A repo already present takes the newest :class:`RepoConfig` (current
    baseline); a tag reported again replaces its older entry.
    """

    by_repo: dict[str, Finding] = {f.repo.name: f for f in stored}
    for finding in new:
        previous = by_repo.get(finding.repo.name)
        tags = {nt.tag: nt for nt in previous.new_tags} if previous else {}
        tags.update({nt.tag: nt for nt in finding.new_tags})
        ordered = sorted(tags.values(), key=lambda nt: tag_sort_key(nt.tag))
        by_repo[finding.repo.name] = Finding(repo=finding.repo, new_tags=ordered)
    return list(by_repo.values())


def prune(findings: list[Finding], now: datetime) -> list[Finding]:
    """Drop tags detected more than :data:`RETENTION` ago, then empty repos."""

    cutoff = now - RETENTION
    kept: list[Finding] = []
    for finding in findings:
        fresh = [
            nt for nt in finding.new_tags if nt.detected_at is not None and nt.detected_at >= cutoff
        ]
        if fresh:
            kept.append(finding.model_copy(update={"new_tags": fresh}))
    return kept


# --- I/O -----------------------------------------------------------------


def load_report(path: Path) -> SetReport | None:
    """Load one report file (``None`` when it does not exist)."""

    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return SetReport(
        release_set=data["release_set"],
        generated_at=data.get("generated_at"),
        findings=[Finding.model_validate(item) for item in data.get("findings", [])],
    )


def save_report(path: Path, report: SetReport) -> None:
    """Write ``report`` as stable, diff-friendly JSON."""

    payload = {
        "release_set": report.release_set,
        "generated_at": report.generated_at,
        "findings": [f.model_dump(mode="json", by_alias=True) for f in report.findings],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _set_sort_key(release_set: str) -> tuple:
    try:
        return (0, tuple(int(part) for part in release_set.split(".")))
    except ValueError:
        return (1, (release_set,))


def load_reports(directory: str | Path) -> list[SetReport]:
    """Load every ``report-*.json`` under ``directory``, sorted by release set."""

    reports = [
        report
        for path in sorted(Path(directory).glob(f"**/{REPORT_GLOB}"))
        if (report := load_report(path)) is not None
    ]
    reports.sort(key=lambda r: _set_sort_key(r.release_set))
    return reports
