"""Reporter agent tests (Milestone 6).

The reporter consumes only structured findings and renders a machine-readable
JSON summary and a short TL;DR. Tests build findings directly (no graph) and
pin: release-notes / diff / CVE links, JSON validity + computed fields, the
TL;DR, and the empty (quiet) run.
"""

from __future__ import annotations

import json

from tracker.agents import reporter
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
        canonical_branch="canonical/x/stable",
        current_upstream_tag=tag,
    )


def _high_finding() -> Finding:
    tag = flagged_preflight_finding(
        "v1.36.6",
        risk="high",
        summary="kubelet eviction default changed; CVE fix included.",
        trial_merge=TrialMerge(result="conflict", conflicting_paths=["pkg/kubelet/x.go"]),
        cve_refs_found=True,
        highlights=[Highlight(kind="behaviour_change", area="kubelet", detail="eviction default")],
        dependencies=[
            Dependency(
                name="golang.org/x/net",
                **{"from": "vA", "to": "vB"},
                reason="cve_fix",
                cve="CVE-2026-2222",
            )
        ],
        notes_for_reviewer="Confirm our eviction patch still applies.",
    )
    tag.analysis = Analysis(
        cves=[Cve(cve="CVE-2026-1111", severity="high", affects="kube-apiserver", detail="bypass")],
        conflicts=[
            Conflict(path="pkg/kubelet/x.go", cause="patch overlap", resolution_hint="reapply")
        ],
    )
    return Finding(repo=_repo("kubernetes/kubernetes", "v1.36.5"), new_tags=[tag])


def _medium_finding() -> Finding:
    tag = flagged_preflight_finding(
        "v3.6.14",
        risk="medium",
        summary="Trial merge conflicts in 1 file(s).",
        trial_merge=TrialMerge(result="conflict", conflicting_paths=["server/x.go"]),
    )
    return Finding(repo=_repo("etcd-io/etcd", "v3.6.13"), new_tags=[tag])


def _clean_finding() -> Finding:
    return Finding(
        repo=_repo("coredns/coredns", "v1.14.6"),
        new_tags=[clean_tag_finding("v1.14.7", summary="Trial merge clean; 3 commit(s).")],
    )


# --- link helpers --------------------------------------------------------


def test_link_helpers():
    repo = _repo("coredns/coredns", "v1.14.6")
    assert reporter.release_notes_url(repo, "v1.14.7") == (
        "https://github.com/coredns/coredns/releases/tag/v1.14.7"
    )
    assert reporter.compare_url(repo, "v1.14.7") == (
        "https://github.com/coredns/coredns/compare/v1.14.6...v1.14.7"
    )
    assert reporter.cve_url("CVE-2026-1111") == ("https://nvd.nist.gov/vuln/detail/CVE-2026-1111")


def test_cve_url_is_scheme_aware():
    assert reporter.cve_url("CVE-2026-1111") == "https://nvd.nist.gov/vuln/detail/CVE-2026-1111"
    assert reporter.cve_url("GHSA-6vch-q96h-7gc3") == (
        "https://github.com/advisories/GHSA-6vch-q96h-7gc3"
    )
    assert reporter.cve_url("GO-2024-0001") == "https://pkg.go.dev/vuln/GO-2024-0001"
    assert reporter.cve_url("USN-1234-1") == "https://ubuntu.com/security/notices/USN-1234-1"


# --- json ----------------------------------------------------------------


def test_json_is_valid_with_summary_and_computed_fields():
    data = json.loads(reporter.render_json([_high_finding(), _clean_finding()]))
    assert data["summary"]["repos"] == 2
    assert data["summary"]["tags"] == 2
    assert data["summary"]["risk_counts"]["high"] == 1
    assert data["summary"]["risk_counts"]["low"] == 1
    # Computed fields + dependency aliases survive serialization.
    k8s = next(f for f in data["findings"] if f["repo"]["name"] == "kubernetes/kubernetes")
    assert k8s["tracked_version"] == "1.36.x"
    assert k8s["last_merged_tag"] == "v1.36.5"
    dep = k8s["new_tags"][0]["dependencies"][0]
    assert dep["from"] == "vA" and dep["to"] == "vB"


def test_json_empty_run():
    data = json.loads(reporter.render_json([]))
    assert data["summary"]["repos"] == 0
    assert data["findings"] == []


# --- tldr ----------------------------------------------------------------


def test_tldr_counts_and_top_item():
    tldr = reporter.render_tldr([_high_finding(), _medium_finding(), _clean_finding()])
    assert "3" in tldr  # 3 repos
    assert "high" in tldr.lower()
    assert "kubernetes/kubernetes" in tldr  # highest-risk item highlighted


def test_tldr_empty_run():
    assert "no new" in reporter.render_tldr([]).lower()


# --- node ----------------------------------------------------------------


def test_reporter_node_emits_json_and_tldr():
    out = reporter.reporter_node({"findings": [_high_finding()]})
    assert json.loads(out["report_json"])["summary"]["repos"] == 1
    assert out["tldr"]
