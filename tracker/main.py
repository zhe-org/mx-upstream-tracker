"""CLI entrypoint for the upstream release tracker.

A single run targets one K8s **release set**, given as the required positional
argument in dot form (e.g. ``python -m tracker.main 1.36``). That selects the
registry ``registries/upstream-tracker-1-36.yaml`` and the snapshot
``state/processed-1-36.json``; the argument is exported as ``RELEASE_SET`` so
every downstream ``load_settings()`` call resolves the same paths.

In M2 the orchestrator is complete but the per-repo sub-graph (preflight/
analyzer, M4/M5) and reporter (M6) are still placeholders, so we run the
orchestrator pipeline and finalize. The reporter step slots in between dispatch
and finalize once M6 lands.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from tracker.config import REGISTRIES_DIR, load_settings, registry_path_for
from tracker.finalize import finalize_run
from tracker.graph import run_orchestrator
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
        f"k8s-upstream-tracker: release_set={release_set} "
        f"model={settings.llm_model} tracker={settings.tracker_path}"
    )

    state = run_orchestrator()
    findings = state.get("findings", [])

    if not findings:
        print("Quiet run: no new upstream tags on any tracked line.")
        return

    for finding in findings:
        tags = ", ".join(entry.tag for entry in finding.new_tags)
        line = line_label(finding.repo.current_upstream_tag)
        print(f"  {finding.repo.name} ({line}): {tags}")

    finalize_run(state.get("repos", []), findings, settings)
    print(
        f"Reported {len(findings)} repo(s). Updated {settings.state_path.name} snapshot "
        f"and advanced current_upstream_tag in {settings.tracker_path.name}."
    )


if __name__ == "__main__":
    main()
