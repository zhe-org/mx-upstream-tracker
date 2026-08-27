"""Mattermost notification (Milestone 7 delivery).

After a nightly run publishes the combined GitHub Pages report, this module
posts one brief message to a Mattermost channel via an **incoming webhook**,
listing the components that got new tags and linking to the deployed page.

It reuses :func:`tracker.site.load_reports` so the notifier consumes exactly the
same per-release-set artifacts (``report.json`` + ``meta.json``) the site
generator does — one consolidated message across every set, posted after the
Pages deploy so the link is live.

Delivery is deliberately decoupled from :class:`tracker.config.Settings`: the
webhook URL is read straight from ``MATTERMOST_WEBHOOK_URL``. If that secret is
unset (e.g. a fork without the webhook), or the run is quiet (no new tags on any
set), the notifier is a clean no-op — it never fails the workflow.

CLI::

    python -m tracker.notify <download_dir> --page-url <url>

``<download_dir>`` is the directory of downloaded per-set report artifacts (the
same input the site generator takes).
"""

from __future__ import annotations

import argparse
import os

import httpx

from tracker.agents.reporter import _RISK_ORDER, _risk_counts
from tracker.models import Finding
from tracker.site import SetReport, _newest_tag, load_reports

WEBHOOK_ENV = "MATTERMOST_WEBHOOK_URL"
_POST_TIMEOUT = 30.0


# --- message building ----------------------------------------------------


def _component_line(finding: Finding) -> str:
    """One bullet per component: ``owner/repo baseline -> newest tag``."""
    repo = finding.repo
    return f"- `{repo.name}` {repo.current_upstream_tag} → {_newest_tag(finding)}"


def _risk_tally(findings: list[Finding]) -> str:
    counts = _risk_counts(findings)
    return ", ".join(f"{counts[risk]} {risk}" for risk in _RISK_ORDER)


def build_message(reports: list[SetReport], page_url: str | None) -> str | None:
    """Build the Mattermost markdown message, or ``None`` on a quiet run.

    A "quiet run" is one where no release set has any component with new tags;
    in that case we stay silent (return ``None``) rather than post noise.
    """

    active = [r for r in reports if r.findings]
    if not active:
        return None

    all_findings = [f for r in active for f in r.findings]
    n_components = len(all_findings)

    lines = [
        f"**Upstream tracker** — {n_components} component(s) with new tags",
        "",
    ]
    for report in active:
        lines.append(f"**Kubernetes {report.release_set}**")
        for finding in report.findings:
            lines.append(_component_line(finding))
        lines.append("")

    footer = f"Risk: {_risk_tally(all_findings)}"
    if page_url:
        footer += f" · [View full report]({page_url})"
    lines.append(footer)

    return "\n".join(lines)


# --- delivery ------------------------------------------------------------


def post_message(webhook_url: str, text: str) -> None:
    """POST ``text`` to a Mattermost incoming webhook; raise on HTTP error."""
    response = httpx.post(webhook_url, json={"text": text}, timeout=_POST_TIMEOUT)
    response.raise_for_status()


# --- CLI -----------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="tracker.notify",
        description="Post a brief Mattermost notification for the nightly report.",
    )
    parser.add_argument(
        "download_dir",
        help="Directory of downloaded per-set report artifacts (report.json + meta.json).",
    )
    parser.add_argument(
        "--page-url",
        default=None,
        help="URL of the deployed report page to link to.",
    )
    args = parser.parse_args(argv)

    webhook = os.getenv(WEBHOOK_ENV)
    if not webhook:
        print(f"{WEBHOOK_ENV} is not set; skipping Mattermost notification.")
        return

    reports = load_reports(args.download_dir)
    message = build_message(reports, args.page_url)
    if message is None:
        print("Quiet run: no new tags on any set; nothing to notify.")
        return

    post_message(webhook, message)
    print("Posted release notification to Mattermost.")


if __name__ == "__main__":
    main()
