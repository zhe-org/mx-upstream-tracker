"""Static site generator for the tracker's reports.

Renders the per-release-set report store (``reports/report-<set>.json``, see
:mod:`tracker.reports`) into a single, self-contained HTML page — embedded CSS +
a little vanilla JS for risk filtering and expand/collapse — suitable for
GitHub Pages. Tags older than the 7-day retention window are dropped at build
time, so the page stays current even when a set has not run since.

The page is organised for scanning: a top overview with per-set risk counts,
then one collapsible section per release set, each with a risk-ranked **version
bump** table (From → To per component, plus when the tag was detected) and
drill-down cards for the flagged tags. All dynamic / LLM-authored text is
HTML-escaped.

CLI::

    python -m tracker.site <reports_dir> <out_dir>

``index.html`` is written to ``<out_dir>``.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from tracker.agents.reporter import (
    _RANK,
    _finding_risk,
    _ranked,
    compare_url,
    cve_url,
    release_notes_url,
)
from tracker.models import Finding, NewTagFinding, RepoConfig
from tracker.reports import SetReport, load_reports, prune

_MERGE_LABEL = {"clean": "clean", "conflict": "conflict", "error": "error"}

# --- small render helpers -----------------------------------------------


def _risk_badge(risk: str) -> str:
    return f'<span class="badge risk-{escape(risk)}">{escape(risk)}</span>'


def _merge_badge(result: str) -> str:
    label = _MERGE_LABEL.get(result, result)
    return f'<span class="badge merge-{escape(result)}">{escape(label)}</span>'


def _has_detail(tag: NewTagFinding) -> bool:
    return bool(tag.highlights or tag.dependencies or tag.analysis or tag.notes_for_reviewer)


def _newest_tag(finding: Finding) -> str:
    # new_tags are kept oldest -> newest, so the last is the newest.
    return finding.new_tags[-1].tag if finding.new_tags else finding.repo.current_upstream_tag


def _base(repo: RepoConfig, tag: NewTagFinding) -> str:
    """The fork baseline ``tag`` was compared against when it was detected."""
    return tag.baseline or repo.current_upstream_tag


def _detected(tag: NewTagFinding) -> str:
    return tag.detected_at.strftime("%Y-%m-%d") if tag.detected_at else ""


# --- summary table -------------------------------------------------------


def _summary_rows(findings: list[Finding]) -> list[str]:
    rows: list[str] = []
    for finding in _ranked(findings):
        repo = finding.repo
        for tag in sorted(finding.new_tags, key=lambda t: -_RANK[t.risk]):
            base = _base(repo, tag)
            merge = tag.preflight.trial_merge.result
            summary = escape(tag.summary.strip())
            rows.append(
                "<tr data-risk="
                + f'"{escape(tag.risk)}">'
                + f"<td>{_risk_badge(tag.risk)}</td>"
                + f'<td class="component"><a href="{escape(repo.upstream)}">'
                + f"{escape(repo.name)}</a></td>"
                + f'<td class="ver from">{escape(base)}</td>'
                + '<td class="arrow">&rarr;</td>'
                + '<td class="ver to">'
                + f'<a href="{escape(release_notes_url(repo, tag.tag))}">{escape(tag.tag)}</a>'
                + f' <a class="difflink" href="{escape(compare_url(repo, base, tag.tag))}"'
                + ' title="View diff">diff</a></td>'
                + f"<td>{_merge_badge(merge)}</td>"
                + f'<td class="summary">{summary}</td>'
                + f'<td class="detected">{escape(_detected(tag))}</td>'
                + "</tr>"
            )
    return rows


def _summary_table(findings: list[Finding]) -> list[str]:
    return [
        '<table class="summary">',
        "<thead><tr>",
        "<th>Risk</th><th>Component</th><th>From</th><th></th><th>To</th>",
        "<th>Trial merge</th><th>Summary</th><th>Detected</th>",
        "</tr></thead>",
        "<tbody>",
        *_summary_rows(findings),
        "</tbody>",
        "</table>",
    ]


# --- detail cards --------------------------------------------------------


def _tag_detail(repo: RepoConfig, tag: NewTagFinding) -> list[str]:
    out = [f'<div class="tag-detail" data-risk="{escape(tag.risk)}">']
    out.append(
        f"<h4>{escape(tag.tag)} {_risk_badge(tag.risk)} "
        f'<a class="difflink" href="{escape(compare_url(repo, _base(repo, tag), tag.tag))}">'
        "diff</a> "
        f'<a class="difflink" href="{escape(release_notes_url(repo, tag.tag))}">'
        "release notes</a></h4>"
    )
    out.append(f"<p>{escape(tag.summary.strip())}</p>")

    if tag.highlights:
        out.append('<div class="block"><span class="block-label">Highlights</span><ul>')
        for h in tag.highlights:
            scope = h.area or h.component or ""
            scope = f" [{escape(scope)}]" if scope else ""
            ref = f" ({escape(h.upstream_ref)})" if h.upstream_ref else ""
            out.append(f"<li><b>{escape(h.kind)}</b>{scope}: {escape(h.detail)}{ref}</li>")
        out.append("</ul></div>")

    if tag.dependencies:
        out.append('<div class="block"><span class="block-label">Dependencies</span><ul>')
        for d in tag.dependencies:
            cve = f" &mdash; {escape(d.cve)}" if d.cve else ""
            out.append(
                f"<li><code>{escape(d.name)}</code>: {escape(d.from_)} &rarr; "
                f"{escape(d.to)} <em>({escape(d.reason)}{cve})</em></li>"
            )
        out.append("</ul></div>")

    if tag.analysis and tag.analysis.cves:
        out.append('<div class="block"><span class="block-label">CVEs</span><ul>')
        for c in tag.analysis.cves:
            out.append(
                f'<li><a href="{escape(cve_url(c.cve))}">{escape(c.cve)}</a> '
                f"<b>({escape(c.severity)})</b> &mdash; affects {escape(c.affects)}: "
                f"{escape(c.detail)}</li>"
            )
        out.append("</ul></div>")

    if tag.analysis and tag.analysis.conflicts:
        out.append('<div class="block"><span class="block-label">Conflicts</span><ul>')
        for c in tag.analysis.conflicts:
            out.append(
                f"<li><code>{escape(c.path)}</code>: {escape(c.cause)} &rarr; "
                f"{escape(c.resolution_hint)}</li>"
            )
        out.append("</ul></div>")

    if tag.notes_for_reviewer:
        out.append(
            '<div class="block notes"><span class="block-label">Notes for reviewer</span>'
            f"<p>{escape(tag.notes_for_reviewer.strip())}</p></div>"
        )

    out.append("</div>")
    return out


def _detail_cards(findings: list[Finding]) -> list[str]:
    cards: list[str] = []
    for finding in _ranked(findings):
        repo = finding.repo
        detailed = [t for t in finding.new_tags if _has_detail(t)]
        if not detailed:
            continue
        risk = _finding_risk(finding)
        title = (
            f"{escape(repo.name)} &nbsp;"
            f'<span class="mono">{escape(repo.current_upstream_tag)} &rarr; '
            f"{escape(_newest_tag(finding))}</span> {_risk_badge(risk)}"
        )
        cards.append(f'<details class="card" data-risk="{escape(risk)}" open>')
        cards.append(f"<summary>{title}</summary>")
        cards.append(
            f'<div class="card-body"><p class="sub">Fork branch '
            f"<code>{escape(repo.canonical_branch)}</code></p>"
        )
        for tag in sorted(detailed, key=lambda t: -_RANK[t.risk]):
            cards.extend(_tag_detail(repo, tag))
        cards.append("</div></details>")
    return cards


# --- overview + sections -------------------------------------------------


def _overview(reports: list[SetReport]) -> list[str]:
    total_tags = sum(sum(len(f.new_tags) for f in r.findings) for r in reports)
    total_repos = sum(len(r.findings) for r in reports)

    out = ['<section class="overview">']
    out.append(
        '<div class="totals">'
        f'<div class="total"><span class="num">{len(reports)}</span>'
        '<span class="lbl">release sets</span></div>'
        f'<div class="total"><span class="num">{total_repos}</span>'
        '<span class="lbl">components</span></div>'
        f'<div class="total"><span class="num">{total_tags}</span>'
        '<span class="lbl">new tags</span></div>'
        "</div>"
    )
    out.append('<div class="set-cards">')
    for r in reports:
        n_tags = sum(len(f.new_tags) for f in r.findings)
        out.append(
            f'<a class="set-card" href="#set-{escape(r.release_set)}">'
            f'<span class="set-name">Kubernetes {escape(r.release_set)}</span>'
            f'<span class="set-meta">{n_tags} new tag(s)</span></a>'
        )
    out.append("</div>")
    out.append("</section>")
    return out


def _set_section(report: SetReport) -> list[str]:
    rs = report.release_set
    n_tags = sum(len(f.new_tags) for f in report.findings)
    when = f" &middot; updated {escape(report.generated_at)}" if report.generated_at else ""

    out = [f'<section class="release-set" id="set-{escape(rs)}">']
    out.append(
        f'<div class="set-header"><h2>Kubernetes {escape(rs)}</h2>'
        f'<span class="set-sub">{n_tags} new tag(s){when}</span></div>'
    )

    if not report.findings:
        out.append(
            '<p class="empty">No new upstream tags in the last 7 days — '
            "all components are up to date.</p>"
        )
        out.append("</section>")
        return out

    out.extend(_summary_table(report.findings))

    cards = _detail_cards(report.findings)
    if cards:
        out.append('<h3 class="detail-heading">Flagged components</h3>')
        out.extend(cards)

    out.append("</section>")
    return out


# --- page ----------------------------------------------------------------


def _latest_generated(reports: list[SetReport]) -> str | None:
    stamps = [r.generated_at for r in reports if r.generated_at]
    return max(stamps) if stamps else None


def render_page(reports: list[SetReport]) -> str:
    """Render the full combined HTML page for ``reports``."""

    body: list[str] = []
    generated = _latest_generated(reports)
    subtitle = f"Generated {escape(generated)}" if generated else "No reports available yet"

    body.append('<header class="top">')
    body.append('<div class="wrap">')
    body.append("<h1>Upstream Release Review</h1>")
    body.append(f'<p class="subtitle">{subtitle}</p>')
    body.append(
        '<div class="controls">'
        '<div class="filters" role="group" aria-label="Filter by risk">'
        '<span class="filter-label">Filter:</span>'
        '<button class="filter-btn active" data-level="all">All</button>'
        '<button class="filter-btn" data-level="high">High</button>'
        '<button class="filter-btn" data-level="medium">Medium</button>'
        '<button class="filter-btn" data-level="low">Low</button>'
        "</div>"
        '<div class="toggles">'
        '<button class="toggle-btn" data-open="true">Expand all</button>'
        '<button class="toggle-btn" data-open="false">Collapse all</button>'
        "</div>"
        "</div>"
    )
    body.append("</div></header>")

    body.append('<main class="wrap">')
    if not reports:
        body.append('<p class="empty">No reports yet.</p>')
    else:
        body.extend(_overview(reports))
        for report in reports:
            body.extend(_set_section(report))
    body.append("</main>")

    body.append(
        '<footer class="wrap"><p>mx-upstream-tracker &middot; '
        "risk is derived from the trial merge + advisory scan; deep analysis is "
        "LLM-assisted. Always review the linked diffs before merging.</p></footer>"
    )

    return _HTML_SHELL.format(css=_CSS, js=_JS, body="\n".join(body))


# --- CLI -----------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="tracker.site",
        description="Render the combined HTML report site from the report store.",
    )
    parser.add_argument("reports_dir", help="Directory holding report-<set>.json files.")
    parser.add_argument("out_dir", help="Output directory for the generated site.")
    args = parser.parse_args(argv)

    now = datetime.now(UTC)
    reports = [
        SetReport(r.release_set, r.generated_at, prune(r.findings, now))
        for r in load_reports(args.reports_dir)
    ]
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render_page(reports), encoding="utf-8")
    # Serve as-is (no Jekyll processing) on GitHub Pages.
    (out / ".nojekyll").write_text("", encoding="utf-8")

    sets = ", ".join(r.release_set for r in reports) or "none"
    print(f"Wrote {out / 'index.html'} for release set(s): {sets}.")


# Kept last: large static assets, out of the way of the logic above.

_HTML_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Upstream Release Review</title>
<style>
{css}
</style>
</head>
<body>
{body}
<script>
{js}
</script>
</body>
</html>
"""

_CSS = """
:root {
  --bg: #f6f8fa; --fg: #1f2328; --muted: #656d76; --line: #d0d7de;
  --card: #ffffff; --accent: #0969da;
  --high-bg: #ffebe9; --high-fg: #cf222e;
  --med-bg: #fff8c5; --med-fg: #7d4e00;
  --low-bg: #dafbe1; --low-fg: #1a7f37;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
}
.wrap { max-width: 1120px; margin: 0 auto; padding: 0 20px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .9em; }

.top {
  background: linear-gradient(180deg, #ffffff, #f0f3f6);
  border-bottom: 1px solid var(--line); padding: 22px 0 16px;
  position: sticky; top: 0; z-index: 10;
}
.top h1 { margin: 0; font-size: 22px; letter-spacing: -0.01em; }
.subtitle { margin: 2px 0 14px; color: var(--muted); font-size: 13px; }
.controls { display: flex; flex-wrap: wrap; gap: 16px; align-items: center; justify-content: space-between; }
.filters, .toggles { display: flex; gap: 6px; align-items: center; }
.filter-label { color: var(--muted); font-size: 13px; margin-right: 2px; }
.filter-btn, .toggle-btn {
  border: 1px solid var(--line); background: #fff; color: var(--fg);
  border-radius: 6px; padding: 4px 10px; font-size: 13px; cursor: pointer;
}
.filter-btn:hover, .toggle-btn:hover { background: #f3f4f6; }
.filter-btn.active { background: var(--fg); color: #fff; border-color: var(--fg); }

.overview { margin: 24px 0 8px; }
.totals { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
.total {
  background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 12px 16px; display: flex; flex-direction: column; min-width: 120px;
}
.total .num { font-size: 24px; font-weight: 700; }
.total .lbl { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }

.set-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
.set-card {
  background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 14px 16px; display: flex; flex-direction: column; gap: 6px; color: var(--fg);
}
.set-card:hover { border-color: var(--accent); text-decoration: none; box-shadow: 0 1px 4px rgba(0,0,0,.06); }
.set-name { font-weight: 600; }
.set-meta { color: var(--muted); font-size: 12px; }

.release-set { margin: 34px 0; scroll-margin-top: 120px; }
.set-header { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; border-bottom: 2px solid var(--line); padding-bottom: 8px; margin-bottom: 14px; }
.set-header h2 { margin: 0; font-size: 19px; }
.set-sub { color: var(--muted); font-size: 13px; }

table.summary { width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
table.summary th, table.summary td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
table.summary th { background: #f0f3f6; font-size: 12px; text-transform: uppercase; letter-spacing: .03em; color: var(--muted); }
table.summary tr:last-child td { border-bottom: none; }
table.summary tbody tr:hover { background: #f9fafb; }
td.component a { font-weight: 600; }
td.ver { white-space: nowrap; }
td.ver.from { color: var(--muted); }
td.arrow { color: var(--muted); padding: 9px 2px; text-align: center; }
td.summary { color: #40464d; }
td.detected { color: var(--muted); white-space: nowrap; font-size: 12px; }
.difflink { font-size: 12px; color: var(--muted); border: 1px solid var(--line); border-radius: 5px; padding: 0 5px; margin-left: 4px; }
.difflink:hover { border-color: var(--accent); text-decoration: none; }

.badge { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 6px; text-transform: uppercase; letter-spacing: .03em; }
.badge.risk-high { background: var(--high-bg); color: var(--high-fg); }
.badge.risk-medium { background: var(--med-bg); color: var(--med-fg); }
.badge.risk-low { background: var(--low-bg); color: var(--low-fg); }
.badge.merge-clean { background: var(--low-bg); color: var(--low-fg); }
.badge.merge-conflict { background: var(--med-bg); color: var(--med-fg); }
.badge.merge-error { background: var(--high-bg); color: var(--high-fg); }

.detail-heading { font-size: 14px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin: 22px 0 10px; }
details.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; margin-bottom: 12px; overflow: hidden; }
details.card > summary { cursor: pointer; padding: 12px 16px; font-weight: 600; list-style: none; display: flex; align-items: center; gap: 8px; }
details.card > summary::-webkit-details-marker { display: none; }
details.card > summary::before { content: "\\25B8"; color: var(--muted); transition: transform .15s; }
details.card[open] > summary::before { transform: rotate(90deg); }
details.card > summary:hover { background: #f9fafb; }
.card-body { padding: 4px 18px 16px; border-top: 1px solid var(--line); }
.card-body .sub { color: var(--muted); font-size: 13px; margin: 10px 0; }
.tag-detail { border-left: 3px solid var(--line); padding: 2px 0 2px 14px; margin: 14px 0; }
.tag-detail h4 { margin: 6px 0; font-size: 15px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.tag-detail p { margin: 6px 0; }
.block { margin: 10px 0; }
.block-label { display: inline-block; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; color: var(--muted); margin-bottom: 2px; }
.block ul { margin: 4px 0; padding-left: 20px; }
.block li { margin: 3px 0; }
.block.notes { background: #fff8e6; border: 1px solid #f0e2b6; border-radius: 8px; padding: 8px 12px; }

.empty { color: var(--muted); background: var(--card); border: 1px dashed var(--line); border-radius: 10px; padding: 18px; text-align: center; }

footer { color: var(--muted); font-size: 12px; margin: 40px auto 30px; }
.hidden { display: none !important; }
"""

_JS = """
(function () {
  function applyFilter(level) {
    document.querySelectorAll('[data-risk]').forEach(function (el) {
      el.classList.toggle('hidden', !(level === 'all' || el.dataset.risk === level));
    });
    document.querySelectorAll('.filter-btn').forEach(function (b) {
      b.classList.toggle('active', b.dataset.level === level);
    });
  }
  document.querySelectorAll('.filter-btn').forEach(function (btn) {
    btn.addEventListener('click', function () { applyFilter(btn.dataset.level); });
  });
  document.querySelectorAll('.toggle-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var open = btn.dataset.open === 'true';
      document.querySelectorAll('details.card').forEach(function (d) { d.open = open; });
    });
  });
})();
"""


if __name__ == "__main__":
    main()
