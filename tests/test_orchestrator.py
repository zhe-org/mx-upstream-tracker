"""Orchestrator + finalize tests (Milestone 2)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tracker import config
from tracker.agents import orchestrator, preflight
from tracker.assembler import clean_tag_finding
from tracker.finalize import finalize_run
from tracker.graph import _worker_count, dispatch, run_repo_subgraph
from tracker.models import EvidenceBundle, Finding, RepoConfig, RepoJob, TrialMerge
from tracker.reports import load_report, stamp
from tracker.state import load_scanned, save_scanned


def _fake_preflight(monkeypatch) -> None:
    """Stub preflight's network seam so dispatch/subgraph tests stay offline.

    ``gather_evidence`` is the only part that touches GitHub / git; stubbing it
    with a clean trial merge lets the real ``decide`` produce a clean finding
    without any network.
    """
    monkeypatch.setattr(
        preflight,
        "gather_evidence",
        lambda repo, tag, **kw: (EvidenceBundle(), TrialMerge(result="clean")),
    )


def _repo(name: str, tag: str) -> RepoConfig:
    return RepoConfig(
        name=name,
        upstream=f"https://github.com/{name}",
        canonical_repo=f"https://github.com/canonical/mx-{name.replace('/', '-')}",
        canonical_branch="canonical/x/stable",
        current_upstream_tag=tag,
    )


def _settings(tmp_path: Path) -> config.Settings:
    return replace(
        config.load_settings(),
        tracker_path=tmp_path / "upstream-tracker.yaml",
        state_path=tmp_path / "processed.json",
        reports_path=tmp_path / "report.json",
    )


# --- discover_releases_node ---------------------------------------------


def test_discover_builds_jobs_for_new_tags(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STATE_PATH", str(tmp_path / "processed.json"))
    repos = [_repo("coredns/coredns", "v1.14.6"), _repo("etcd-io/etcd", "v3.6.13")]
    fake_tags = {
        "coredns/coredns": ["v1.14.6", "v1.14.7", "v1.14.8", "v1.15.0"],
        "etcd-io/etcd": ["v3.6.13"],  # nothing newer
    }
    monkeypatch.setattr(orchestrator, "list_tags", lambda repo: fake_tags[repo])

    out = orchestrator.discover_releases_node({"repos": repos})
    jobs = out["jobs"]
    assert len(jobs) == 1
    assert jobs[0].repo.name == "coredns/coredns"
    assert jobs[0].new_tags == ["v1.14.7", "v1.14.8"]


def test_discover_quiet_run(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STATE_PATH", str(tmp_path / "processed.json"))
    repos = [_repo("coredns/coredns", "v1.14.6")]
    monkeypatch.setattr(orchestrator, "list_tags", lambda repo: ["v1.14.6"])

    assert orchestrator.discover_releases_node({"repos": repos})["jobs"] == []


def test_discover_skips_tags_already_scanned(monkeypatch, tmp_path: Path):
    # Fork still on v1.14.6, but v1.14.7 was reported last night: only v1.14.8 is new.
    state = tmp_path / "processed.json"
    save_scanned({"coredns/coredns": "v1.14.7"}, state)
    monkeypatch.setenv("STATE_PATH", str(state))
    monkeypatch.setattr(orchestrator, "list_tags", lambda repo: ["v1.14.7", "v1.14.8"])

    jobs = orchestrator.discover_releases_node({"repos": [_repo("coredns/coredns", "v1.14.6")]})
    assert jobs["jobs"][0].new_tags == ["v1.14.8"]


def test_discover_baseline_ahead_of_watermark_wins(monkeypatch, tmp_path: Path):
    # Fork merged v1.14.9 by hand; the older watermark must not resurface v1.14.8/9.
    state = tmp_path / "processed.json"
    save_scanned({"coredns/coredns": "v1.14.7"}, state)
    monkeypatch.setenv("STATE_PATH", str(state))
    monkeypatch.setattr(orchestrator, "list_tags", lambda repo: ["v1.14.8", "v1.14.9", "v1.14.10"])

    jobs = orchestrator.discover_releases_node({"repos": [_repo("coredns/coredns", "v1.14.9")]})
    assert jobs["jobs"][0].new_tags == ["v1.14.10"]


def test_discover_ignores_watermark_on_other_line(monkeypatch, tmp_path: Path):
    # A stale watermark from an older fork line must not hide the new line's tags.
    state = tmp_path / "processed.json"
    save_scanned({"coredns/coredns": "v1.13.9"}, state)
    monkeypatch.setenv("STATE_PATH", str(state))
    monkeypatch.setattr(orchestrator, "list_tags", lambda repo: ["v1.14.7"])

    jobs = orchestrator.discover_releases_node({"repos": [_repo("coredns/coredns", "v1.14.6")]})
    assert jobs["jobs"][0].new_tags == ["v1.14.7"]


# --- dispatch ------------------------------------------------------------


def test_dispatch_fans_out_one_finding_per_job(monkeypatch):
    _fake_preflight(monkeypatch)
    jobs = [
        RepoJob(repo=_repo("coredns/coredns", "v1.14.6"), new_tags=["v1.14.7"]),
        RepoJob(repo=_repo("etcd-io/etcd", "v3.6.13"), new_tags=["v3.6.14", "v3.6.15"]),
    ]
    findings = dispatch({"jobs": jobs})["findings"]
    assert len(findings) == 2

    coredns = next(f for f in findings if f.repo.name == "coredns/coredns")
    assert coredns.repo.current_upstream_tag == "v1.14.6"
    assert coredns.repo.canonical_repo.startswith("https://github.com/canonical/")
    assert [nt.tag for nt in coredns.new_tags] == ["v1.14.7"]


def test_run_repo_subgraph_assembles_per_tag_from_preflight(monkeypatch):
    # The sub-graph runs preflight per tag; the assembled finding must reflect
    # the real preflight output (not the old pending placeholder).
    _fake_preflight(monkeypatch)
    job = RepoJob(repo=_repo("coredns/coredns", "v1.14.6"), new_tags=["v1.14.8", "v1.14.7"])

    finding = run_repo_subgraph(job)
    # Tags come back sorted oldest -> newest, each carrying the preflight summary.
    assert [nt.tag for nt in finding.new_tags] == ["v1.14.7", "v1.14.8"]
    assert all(nt.summary.startswith("Trial merge clean") for nt in finding.new_tags)
    assert all(nt.preflight.decision == "clean" for nt in finding.new_tags)


def test_dispatch_quiet_run_no_findings():
    assert dispatch({"jobs": []})["findings"] == []


def test_dispatch_parallel_preserves_order(monkeypatch):
    _fake_preflight(monkeypatch)
    # Force the parallel path (>1 worker) regardless of the machine's core count;
    # results must come back in input order despite arbitrary completion order.
    monkeypatch.setattr(
        "tracker.graph.load_settings",
        lambda: replace(config.load_settings(), dispatch_max_workers=8),
    )
    names = [f"owner/repo-{i:02d}" for i in range(12)]
    jobs = [RepoJob(repo=_repo(name, "v1.0.0"), new_tags=["v1.0.1"]) for name in names]

    findings = dispatch({"jobs": jobs})["findings"]
    assert [f.repo.name for f in findings] == names


# --- _worker_count -------------------------------------------------------


def test_worker_count_defaults_to_cpu_bounded_by_jobs():
    assert _worker_count(n_jobs=3, configured=None, cpu=8) == 3
    assert _worker_count(n_jobs=20, configured=None, cpu=8) == 8


def test_worker_count_respects_configured_override():
    assert _worker_count(n_jobs=20, configured=4, cpu=64) == 4
    assert _worker_count(n_jobs=2, configured=4, cpu=64) == 2  # never exceed jobs


def test_worker_count_never_below_one():
    assert _worker_count(n_jobs=5, configured=None, cpu=None) == 1
    assert _worker_count(n_jobs=1, configured=0, cpu=8) == 1


# --- finalize_run --------------------------------------------------------


NOW = datetime(2026, 10, 2, 9, tzinfo=UTC)


def test_finalize_advances_watermark_and_keeps_other_repos(tmp_path: Path):
    settings = _settings(tmp_path)
    save_scanned({"etcd-io/etcd": "v3.6.13", "coredns/coredns": "v1.14.6"}, settings.state_path)
    findings = [
        Finding(
            repo=_repo("coredns/coredns", "v1.14.6"),
            new_tags=[
                clean_tag_finding("v1.14.8-rc.0", summary="rc"),
                clean_tag_finding("v1.14.7", summary="bugfix"),
            ],
        ),
    ]

    finalize_run(stamp(findings, NOW), settings, "1.36", NOW)

    # Newest by semver (v1.14.8-rc.0 > v1.14.7); repos without new tags keep their entry.
    assert load_scanned(settings.state_path) == {
        "coredns/coredns": "v1.14.8-rc.0",
        "etcd-io/etcd": "v3.6.13",
    }


def test_finalize_accumulates_report_and_expires_old_tags(tmp_path: Path):
    settings = _settings(tmp_path)
    earlier = [
        Finding(
            repo=_repo("coredns/coredns", "v1.14.6"),
            new_tags=[clean_tag_finding("v1.14.7", summary="a")],
        ),
        Finding(
            repo=_repo("etcd-io/etcd", "v3.6.12"),
            new_tags=[clean_tag_finding("v3.6.13", summary="b")],
        ),
    ]
    finalize_run(
        stamp(earlier[:1], NOW - timedelta(days=3)), settings, "1.36", NOW - timedelta(days=3)
    )
    finalize_run(
        stamp(earlier[1:], NOW - timedelta(days=8)), settings, "1.36", NOW - timedelta(days=8)
    )

    today = [
        Finding(
            repo=_repo("coredns/coredns", "v1.14.6"),
            new_tags=[clean_tag_finding("v1.14.8", summary="c")],
        )
    ]
    finalize_run(stamp(today, NOW), settings, "1.36", NOW)

    report = load_report(settings.reports_path)
    assert report is not None and report.release_set == "1.36"
    # coredns keeps the 3-day-old tag next to today's; etcd's 8-day-old tag expired.
    assert {f.repo.name: [nt.tag for nt in f.new_tags] for f in report.findings} == {
        "coredns/coredns": ["v1.14.7", "v1.14.8"],
    }


def test_finalize_quiet_run_is_noop(tmp_path: Path):
    settings = _settings(tmp_path)
    finalize_run([], settings, "1.36", NOW)
    assert not settings.state_path.exists()
    assert not settings.reports_path.exists()
