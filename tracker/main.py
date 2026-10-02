"""CLI entrypoint for the upstream release tracker.

A single run targets one K8s **release set**, given as the required positional
argument in dot form (e.g. ``python -m tracker.main 1.36``). That selects the
registry ``registries/upstream-tracker-1-36.yaml``, the watermark
``state/processed-1-36.json`` and the report ``reports/report-1-36.json``; the
argument is exported as ``RELEASE_SET`` so every downstream ``load_settings()``
call resolves the same paths.

The run discovers new tags and runs the per-repo sub-graph (preflight gate +
analyzer deep-dive). When it finds new tags it writes this run's findings to
``<artifact_dir>/report-<set>.json`` (feeds the notification), then finalize
merges them into the 7-day report and advances the watermark. A quiet run
writes nothing.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import UTC, datetime

from tracker.agents.reporter import reporter_node
from tracker.config import REGISTRIES_DIR, load_settings, registry_path_for, report_filename
from tracker.finalize import finalize_run
from tracker.graph import run_orchestrator
from tracker.reports import SetReport, save_report, stamp
from tracker.versioning import line_label

# A release set is a K8s minor line in dot form, e.g. "1.36".
_RELEASE_SET_RE = re.compile(r"^\d+\.\d+$")


def _available_release_sets() -> list[str]:
    """Release sets (dot form) for which a registry file exists, sorted."""
    if not REGISTRIES_DIR.exists():
        return []
    prefix = "upstream-tracker-"
    sets = [p.stem[len(prefix) :].replace("-", ".") for p in REGISTRIES_DIR.glob(f"{prefix}*.yaml")]
    return sorted(sets)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tracker",
        description="Track upstream releases for one K8s release set and report.",
    )
    parser.add_argument(
        "release_set",
        help="K8s release set to track, in dot form (e.g. 1.36).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    release_set = args.release_set

    if not _RELEASE_SET_RE.match(release_set):
        sys.exit(f"Invalid release set {release_set!r}: expected dot form like '1.36'.")

    registry = registry_path_for(release_set)
    if not registry.exists():
        available = ", ".join(_available_release_sets()) or "none"
        sys.exit(
            f"No registry for release set {release_set!r} (looked for {registry}).\n"
            f"Available release sets: {available}."
        )

    # Downstream load_settings() calls read RELEASE_SET to resolve per-set paths.
    os.environ["RELEASE_SET"] = release_set

    settings = load_settings()
    print(
        f"mx-upstream-tracker: release_set={release_set} "
        f"model={settings.llm_model} tracker={settings.tracker_path}"
    )

    state = run_orchestrator()
    print(reporter_node(state)["tldr"])

    now = datetime.now(UTC)
    findings = stamp(state.get("findings", []), now)
    if not findings:
        print("Quiet run: nothing written.")
        return

    for finding in findings:
        tags = ", ".join(entry.tag for entry in finding.new_tags)
        line = line_label(finding.repo.current_upstream_tag)
        print(f"  {finding.repo.name} ({line}): {tags}")

    run_report = settings.artifact_dir / report_filename(release_set)
    save_report(run_report, SetReport(release_set, now.isoformat(timespec="seconds"), findings))
    finalize_run(findings, settings, release_set, now)
    print(
        f"Wrote {run_report}; updated {settings.reports_path.name} and {settings.state_path.name}."
    )


if __name__ == "__main__":
    main()
