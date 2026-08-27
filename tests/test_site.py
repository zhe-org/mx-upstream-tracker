"""Static site generator tests.

The site generator aggregates per-release-set reports (``report.json`` +
``meta.json``) into one combined HTML page. Tests build findings directly and
pin: report loading + set ordering, the From/To version-bump table, risk
badges/links, HTML-escaping of dynamic text, and the quiet/empty renderings.
"""

from __future__ import annotations

import json
from pathlib import Path

from tracker import site
from tracker.agents.reporter import render_json
from tracker.assembler import clean_tag_finding, flagged_preflight_finding
from tracker.models import (
    Analysis,
    Conflict,
    Cve,
    Dependency,
    Finding,
    Highlight,
    RepoConfig,
    TrialMerge,
)


def _repo(name: str, tag: str) -> RepoConfig:
    return RepoConfig(
        name=name,
        upstream=f"https://github.com/{name}",
        canonical_repo=f"https://github.com/canonical/mx-{name.replace('/', '-')}",
        canonical_branch="canonical/x-26.04/stable",
        current_upstream_tag=tag,
    )


def _high() -> Finding:
    tag = flagged_preflight_finding(
        "v1.37.1",
        risk="high",
        summary="kubelet eviction default changed; CVE fix.",
        trial_merge=TrialMerge(result="conflict", conflicting_paths=["pkg/kubelet/x.go"]),
        cve_refs_found=True,
        highlights=[Highlight(kind="behaviour_change", area="kubelet", detail="eviction default")],
        dependencies=[
            Dependency(
                name="golang.org/x/net",
                **{"from": "v0.1", "to": "v0.2"},
                reason="cve_fix",
                cve="CVE-2026-2222",
            )
        ],
        notes_for_reviewer="Confirm our eviction patch still applies.",
    )
    tag.analysis = Analysis(
        cves=[Cve(cve="CVE-2026-1111", severity="high", affects="kube-apiserver", detail="bypass")],
        conflicts=[
            Conflict(path="pkg/kubelet/x.go", cause="patch overlap", resolution_hint="redo")
        ],
    )
    return Finding(repo=_repo("kubernetes/kubernetes", "v1.37.0"), new_tags=[tag])


def _clean() -> Finding:
    return Finding(
        repo=_repo("coredns/coredns", "v1.14.6"),
        new_tags=[clean_tag_finding("v1.14.7", summary="Trial merge clean; 3 commit(s).")],
    )


def _write_set(root: Path, release_set: str, findings: list[Finding], *, meta: bool = True) -> None:
    d = root / f"release-report-{release_set}-7"
    d.mkdir(parents=True)
    (d / "report.json").write_text(render_json(findings), encoding="utf-8")
    if meta:
        (d / "meta.json").write_text(
            json.dumps(
                {"release_set": release_set, "generated_at_utc": "2026-08-27T09:00:00+00:00"}
            ),
            encoding="utf-8",
        )


# --- loading -------------------------------------------------------------


def test_load_reports_reconstructs_and_sorts(tmp_path: Path):
    _write_set(tmp_path, "1.37", [_high()])
    _write_set(tmp_path, "1.36", [_clean()])

    reports = site.load_reports(tmp_path)
    assert [r.release_set for r in reports] == ["1.36", "1.37"]  # numeric sort
    high = next(r for r in reports if r.release_set == "1.37")
    assert high.findings[0].repo.name == "kubernetes/kubernetes"
    assert high.findings[0].new_tags[0].analysis.cves[0].cve == "CVE-2026-1111"
    assert high.generated_at == "2026-08-27T09:00:00+00:00"


def test_load_reports_falls_back_to_dirname(tmp_path: Path):
    _write_set(tmp_path, "1.40", [_clean()], meta=False)
    reports = site.load_reports(tmp_path)
    assert reports[0].release_set == "1.40"  # parsed from folder name
    assert reports[0].generated_at is None


# --- version-bump table (From / To) --------------------------------------


def test_summary_table_shows_from_and_to_bump():
    html = site.render_page([site.SetReport("1.37", None, [_high()])])
    assert "<th>From</th>" in html and "<th>To</th>" in html
    # From = baseline (current_upstream_tag), To = new tag.
    assert '<td class="ver from">v1.37.0</td>' in html
    assert ">v1.37.1</a>" in html  # To links to release notes
    assert "/compare/v1.37.0...v1.37.1" in html  # diff link uses From...To


def test_links_and_badges_present():
    html = site.render_page([site.SetReport("1.37", None, [_high()])])
    assert "https://nvd.nist.gov/vuln/detail/CVE-2026-1111" in html
    assert 'class="badge risk-high"' in html
    assert 'class="badge merge-conflict"' in html
    assert "Notes for reviewer" in html
    assert "golang.org/x/net" in html  # dependency bump surfaced


# --- escaping ------------------------------------------------------------


def test_dynamic_text_is_html_escaped():
    tag = flagged_preflight_finding(
        "v1.2.3",
        risk="high",
        summary="<script>alert('x')</script> & more",
        trial_merge=TrialMerge(result="error"),
        notes_for_reviewer="<img src=x onerror=alert(1)>",
    )
    finding = Finding(repo=_repo("a/b", "v1.2.2"), new_tags=[tag])
    html = site.render_page([site.SetReport("9.9", None, [finding])])
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert" in html
    assert "onerror=alert(1)>" not in html


# --- empty / quiet renderings --------------------------------------------


def test_quiet_set_renders_up_to_date():
    html = site.render_page([site.SetReport("1.36", None, [])])
    assert "up to date" in html
    assert "Kubernetes 1.36" in html


def test_no_reports_renders_placeholder():
    html = site.render_page([])
    assert "No reports were produced" in html


# --- full CLI ------------------------------------------------------------


def test_cli_writes_index_and_nojekyll(tmp_path: Path):
    _write_set(tmp_path, "1.37", [_high()])
    out = tmp_path / "site"
    site.main([str(tmp_path), str(out)])
    assert (out / "index.html").exists()
    assert (out / ".nojekyll").exists()
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "Upstream Release Review" in html
