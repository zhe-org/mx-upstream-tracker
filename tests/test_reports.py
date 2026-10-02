"""Report store tests: stamping, merging, the 7-day window, and round-trips."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tracker.assembler import clean_tag_finding, flagged_preflight_finding
from tracker.models import Dependency, Finding, RepoConfig, TrialMerge
from tracker.reports import (
    RETENTION,
    SetReport,
    load_report,
    load_reports,
    merge,
    prune,
    save_report,
    stamp,
)

NOW = datetime(2026, 10, 2, 9, tzinfo=UTC)


def _repo(name: str, tag: str) -> RepoConfig:
    return RepoConfig(
        name=name,
        upstream=f"https://github.com/{name}",
        canonical_repo=f"https://github.com/canonical/mx-{name.replace('/', '-')}",
        canonical_branch="canonical/x/stable",
        current_upstream_tag=tag,
    )


def _finding(name: str, base: str, *tags: str) -> Finding:
    return Finding(
        repo=_repo(name, base), new_tags=[clean_tag_finding(t, summary="ok") for t in tags]
    )


def _tags(findings: list[Finding]) -> dict[str, list[str]]:
    return {f.repo.name: [nt.tag for nt in f.new_tags] for f in findings}


# --- stamp ---------------------------------------------------------------


def test_stamp_records_detection_time_and_baseline():
    (finding,) = stamp([_finding("coredns/coredns", "v1.14.6", "v1.14.7")], NOW)
    assert finding.new_tags[0].detected_at == NOW
    assert finding.new_tags[0].baseline == "v1.14.6"


# --- merge ---------------------------------------------------------------


def test_merge_keeps_earlier_tags_and_orders_by_semver():
    stored = stamp([_finding("kubernetes/kubernetes", "v1.38.0-alpha.1", "v1.38.0")], NOW)
    new = stamp([_finding("kubernetes/kubernetes", "v1.38.0-alpha.1", "v1.38.0-rc.1")], NOW)
    assert _tags(merge(stored, new)) == {"kubernetes/kubernetes": ["v1.38.0-rc.1", "v1.38.0"]}


def test_merge_takes_newest_repo_config_and_adds_new_repos():
    stored = stamp([_finding("coredns/coredns", "v1.14.6", "v1.14.7")], NOW - timedelta(days=2))
    new = stamp(
        [
            _finding("coredns/coredns", "v1.14.7", "v1.14.8"),  # fork moved on
            _finding("etcd-io/etcd", "v3.6.12", "v3.6.13"),
        ],
        NOW,
    )
    merged = merge(stored, new)
    assert _tags(merged) == {
        "coredns/coredns": ["v1.14.7", "v1.14.8"],
        "etcd-io/etcd": ["v3.6.13"],
    }
    coredns = merged[0]
    assert coredns.repo.current_upstream_tag == "v1.14.7"
    # Each tag keeps the baseline it was compared against when detected.
    assert [nt.baseline for nt in coredns.new_tags] == ["v1.14.6", "v1.14.7"]


# --- prune ---------------------------------------------------------------


def test_prune_window_boundary():
    at_edge = stamp([_finding("a/a", "v1.0.0", "v1.0.1")], NOW - RETENTION)
    past_edge = stamp([_finding("b/b", "v1.0.0", "v1.0.1")], NOW - RETENTION - timedelta(seconds=1))
    assert _tags(prune(at_edge + past_edge, NOW)) == {"a/a": ["v1.0.1"]}


def test_prune_drops_old_tags_but_keeps_fresh_ones_of_same_repo():
    old = stamp([_finding("a/a", "v1.0.0", "v1.0.1")], NOW - timedelta(days=9))
    fresh = stamp([_finding("a/a", "v1.0.0", "v1.0.2")], NOW - timedelta(days=1))
    assert _tags(prune(merge(old, fresh), NOW)) == {"a/a": ["v1.0.2"]}


def test_prune_drops_unstamped_tags():
    assert prune([_finding("a/a", "v1.0.0", "v1.0.1")], NOW) == []


# --- I/O -----------------------------------------------------------------


def test_save_load_round_trip_preserves_analysis_fields(tmp_path: Path):
    tag = flagged_preflight_finding(
        "v1.36.6",
        risk="high",
        summary="dep bump",
        trial_merge=TrialMerge(result="conflict", conflicting_paths=["go.mod"]),
        dependencies=[Dependency(name="x/net", **{"from": "vA", "to": "vB"}, reason="cve_fix")],
    )
    findings = stamp([Finding(repo=_repo("k/k", "v1.36.5"), new_tags=[tag])], NOW)
    path = tmp_path / "report-1-36.json"
    save_report(path, SetReport("1.36", NOW.isoformat(), findings))

    loaded = load_report(path)
    assert loaded is not None
    assert loaded.findings == findings
    assert loaded.findings[0].new_tags[0].dependencies[0].from_ == "vA"


def test_load_report_missing_returns_none(tmp_path: Path):
    assert load_report(tmp_path / "nope.json") is None


def test_load_reports_finds_nested_files_and_sorts_numerically(tmp_path: Path):
    save_report(tmp_path / "a" / "report-1-9.json", SetReport("1.9", None, []))
    save_report(tmp_path / "b" / "report-1-10.json", SetReport("1.10", None, []))
    (tmp_path / "meta.json").write_text("{}", encoding="utf-8")  # ignored
    assert [r.release_set for r in load_reports(tmp_path)] == ["1.9", "1.10"]
