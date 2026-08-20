"""Reporter agent (Milestone 6).

Consumes structured findings only (never raw diffs) and renders three outputs:

  - :func:`render_markdown` — a risk-ranked human-readable report: a summary
    table (high-risk first) followed by per-repo detail (summary, highlights,
    dependency notes, CVEs, conflicts, reviewer notes) with direct links to
    upstream release notes, the tag-to-tag diff, and CVE entries.
  - :func:`render_json` — a machine-readable summary (risk counts + full
    findings dump) for downstream automation.
  - :func:`render_tldr` — a one-line TL;DR suitable for a chat channel.

The reporter is pure (no I/O, no wall-clock), so it is deterministic and
trivially testable; delivery / publishing is M7.
"""

from __future__ import annotations

import json
from collections import Counter

from tracker.models import Finding, GraphState, NewTagFinding, RepoConfig, Risk
from tracker.versioning import line_label

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
    """NVD detail URL for a CVE id."""
    return f"https://nvd.nist.gov/vuln/detail/{cve}"


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


# --- markdown ------------------------------------------------------------


def _summary_table(findings: list[Finding]) -> list[str]:
    rows = ["| Risk | Repo | Tag | Summary |", "| --- | --- | --- | --- |"]
    for finding in _ranked(findings):
        for nt in sorted(finding.new_tags, key=lambda n: -_RANK[n.risk]):
            summary = nt.summary.replace("\n", " ").strip()
            rows.append(f"| {nt.risk} | {finding.repo.name} | {nt.tag} | {summary} |")
    return rows


def _tag_detail(repo: RepoConfig, nt: NewTagFinding) -> list[str]:
    lines = [f"#### {nt.tag} — risk: {nt.risk}", "", nt.summary.strip(), ""]

    lines.append(
        f"- Links: [release notes]({release_notes_url(repo, nt.tag)}) · "
        f"[diff]({compare_url(repo, nt.tag)})"
    )
    tm = nt.preflight.trial_merge
    merge_line = f"- Trial merge: {tm.result}"
    if tm.conflicting_paths:
        merge_line += f" ({', '.join(tm.conflicting_paths)})"
    lines.append(merge_line)

    if nt.highlights:
        lines.append("- Highlights:")
        for h in nt.highlights:
            scope = h.area or h.component or ""
            scope = f" [{scope}]" if scope else ""
            ref = f" ({h.upstream_ref})" if h.upstream_ref else ""
            lines.append(f"  - {h.kind}{scope}: {h.detail}{ref}")

    if nt.dependencies:
        lines.append("- Dependencies:")
        for d in nt.dependencies:
            cve = f" — {d.cve}" if d.cve else ""
            lines.append(f"  - {d.name}: {d.from_} → {d.to} ({d.reason}{cve})")

    if nt.analysis and nt.analysis.cves:
        lines.append("- CVEs:")
        for c in nt.analysis.cves:
            lines.append(
                f"  - [{c.cve}]({cve_url(c.cve)}) ({c.severity}) — affects {c.affects}: {c.detail}"
            )

    if nt.analysis and nt.analysis.conflicts:
        lines.append("- Conflicts:")
        for c in nt.analysis.conflicts:
            lines.append(f"  - `{c.path}`: {c.cause} → {c.resolution_hint}")

    if nt.notes_for_reviewer:
        lines.append(f"- Notes for reviewer: {nt.notes_for_reviewer.strip()}")

    lines.append("")
    return lines


def render_markdown(findings: list[Finding]) -> str:
    """Render the risk-ranked Markdown report."""

    if not findings:
        return "# Upstream release review\n\nNo new upstream tags on any tracked line.\n"

    counts = _risk_counts(findings)
    ranked = _ranked(findings)
    flagged = counts["high"] + counts["medium"]

    out = ["# Upstream release review", ""]
    out.append(
        f"{len(findings)} repo(s), {_tag_count(findings)} new tag(s): "
        f"{counts['high']} high, {counts['medium']} medium, {counts['low']} low."
    )
    out.append("")
    if flagged == 0:
        out.append("Nothing risky — all trial merges clean and no CVE references.")
        out.append("")

    out.append("## Summary")
    out.append("")
    out.extend(_summary_table(findings))
    out.append("")

    out.append("## Details")
    out.append("")
    for finding in ranked:
        repo = finding.repo
        out.append(f"### {repo.name} ({line_label(repo.current_upstream_tag)})")
        out.append("")
        out.append(
            f"Fork branch `{repo.canonical_branch}`, last merged `{repo.current_upstream_tag}`."
        )
        out.append("")
        for nt in sorted(finding.new_tags, key=lambda n: -_RANK[n.risk]):
            out.extend(_tag_detail(repo, nt))

    return "\n".join(out).rstrip() + "\n"


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
    """Render markdown + JSON + TL;DR from ``state['findings']``."""
    findings = state.get("findings", [])
    return {
        "report_markdown": render_markdown(findings),
        "report_json": render_json(findings),
        "tldr": render_tldr(findings),
    }
