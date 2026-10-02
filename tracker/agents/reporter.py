"""Reporter agent (Milestone 6).

Consumes structured findings only (never raw diffs) and renders:

  - :func:`render_json` — a machine-readable summary (risk counts + full
    findings dump) that feeds the dashboard and notifications.
  - :func:`render_tldr` — a one-line TL;DR suitable for a chat channel.

It also owns the link helpers (release notes, diff, advisory URLs) and the risk
ranking the dashboard reuses.

The reporter is pure (no I/O, no wall-clock), so it is deterministic and
trivially testable; delivery / publishing is M7.
"""

from __future__ import annotations

import json
from collections import Counter

from tracker.models import Finding, GraphState, RepoConfig, Risk

_RANK: dict[Risk, int] = {"high": 2, "medium": 1, "low": 0}
_RISK_ORDER: list[Risk] = ["high", "medium", "low"]


# --- link helpers --------------------------------------------------------


def release_notes_url(repo: RepoConfig, tag: str) -> str:
    """Upstream GitHub release-notes URL for ``tag``."""
    return f"{repo.upstream.rstrip('/')}/releases/tag/{tag}"


def compare_url(repo: RepoConfig, tag: str) -> str:
    """Upstream GitHub compare (diff) URL: last merged tag -> ``tag``."""
    return f"{repo.upstream.rstrip('/')}/compare/{repo.current_upstream_tag}...{tag}"


def cve_url(cve: str) -> str:
    """Detail URL for an advisory id, chosen by scheme.

    Recognises CVE (NVD), GHSA (GitHub Advisories), GO (Go vuln DB) and USN
    (Ubuntu Security Notices); anything else falls back to NVD. Name kept as
    ``cve_url`` for continuity even though it now serves multiple schemes.
    """
    upper = cve.upper()
    if upper.startswith("GHSA-"):
        return f"https://github.com/advisories/{cve}"
    if upper.startswith("GO-"):
        return f"https://pkg.go.dev/vuln/{upper}"
    if upper.startswith("USN-"):
        return f"https://ubuntu.com/security/notices/{upper}"
    return f"https://nvd.nist.gov/vuln/detail/{upper}"


# --- ranking -------------------------------------------------------------


def _finding_risk(finding: Finding) -> Risk:
    """Highest tag risk in a repo finding (drives ranking)."""
    return max((nt.risk for nt in finding.new_tags), key=lambda r: _RANK[r], default="low")


def _ranked(findings: list[Finding]) -> list[Finding]:
    """Findings sorted by risk (high first), then repo name for stability."""
    return sorted(findings, key=lambda f: (-_RANK[_finding_risk(f)], f.repo.name))


def _risk_counts(findings: list[Finding]) -> dict[str, int]:
    counts: Counter[str] = Counter(nt.risk for f in findings for nt in f.new_tags)
    return {risk: counts.get(risk, 0) for risk in _RISK_ORDER}


def _tag_count(findings: list[Finding]) -> int:
    return sum(len(f.new_tags) for f in findings)


# --- json ----------------------------------------------------------------


def render_json(findings: list[Finding]) -> str:
    """Render the machine-readable JSON summary + full findings dump."""

    payload = {
        "summary": {
            "repos": len(findings),
            "tags": _tag_count(findings),
            "risk_counts": _risk_counts(findings),
        },
        "findings": [f.model_dump(by_alias=True) for f in _ranked(findings)],
    }
    return json.dumps(payload, indent=2)


# --- tldr ----------------------------------------------------------------


def render_tldr(findings: list[Finding]) -> str:
    """Render a one-line TL;DR for a chat channel."""

    if not findings:
        return "Upstream tracker: no new upstream tags on any tracked line."

    counts = _risk_counts(findings)
    top = _ranked(findings)[0]
    top_tag = max(top.new_tags, key=lambda n: _RANK[n.risk])
    return (
        f"Upstream tracker: {len(findings)} repo(s), {_tag_count(findings)} new tag(s) — "
        f"{counts['high']} high, {counts['medium']} medium, {counts['low']} low. "
        f"Top: {top.repo.name} {top_tag.tag} ({top_tag.risk})."
    )


# --- node ----------------------------------------------------------------


def reporter_node(state: GraphState) -> GraphState:
    """Render JSON + TL;DR from ``state['findings']``."""
    findings = state.get("findings", [])
    return {
        "report_json": render_json(findings),
        "tldr": render_tldr(findings),
    }
