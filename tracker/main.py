"""CLI entrypoint for the upstream release tracker.

Runs a single tracker pass. In M2 the orchestrator is complete but the
per-repo sub-graph (preflight/analyzer, M4/M5) and reporter (M6) are still
placeholders, so we run the orchestrator pipeline and finalize. The reporter
step slots in between dispatch and finalize once M6 lands.
"""

from __future__ import annotations

from tracker.config import load_settings
from tracker.finalize import finalize_run
from tracker.graph import run_orchestrator
from tracker.versioning import line_label


def main() -> None:
    settings = load_settings()
    print(f"k8s-upstream-tracker: model={settings.llm_model} tracker={settings.tracker_path}")

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
