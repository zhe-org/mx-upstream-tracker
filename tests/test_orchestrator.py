"""Orchestrator + finalize tests (Milestone 2)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from tracker import config
from tracker.agents import orchestrator
from tracker.assembler import clean_tag_finding
from tracker.finalize import finalize_run
from tracker.graph import _worker_count, dispatch
from tracker.models import Finding, RepoConfig, RepoJob
from tracker.registry import load_registry


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
    )


# --- discover_releases_node ---------------------------------------------


def test_discover_builds_jobs_for_new_tags(monkeypatch):
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


def test_discover_quiet_run(monkeypatch):
    repos = [_repo("coredns/coredns", "v1.14.6")]
    monkeypatch.setattr(orchestrator, "list_tags", lambda repo: ["v1.14.6"])

    assert orchestrator.discover_releases_node({"repos": repos})["jobs"] == []


# --- dispatch ------------------------------------------------------------


def test_dispatch_fans_out_one_finding_per_job():
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


def test_dispatch_quiet_run_no_findings():
    assert dispatch({"jobs": []})["findings"] == []


def test_dispatch_parallel_preserves_order(monkeypatch):
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


def test_finalize_writes_snapshot_and_bumps_registry(tmp_path: Path):
    settings = _settings(tmp_path)
    repos = [_repo("coredns/coredns", "v1.14.6"), _repo("etcd-io/etcd", "v3.6.13")]
    # Seed the registry file so finalize can rewrite it.
    from tracker.registry import save_registry

    save_registry(settings.tracker_path, repos)

    findings = [
        Finding(
            repo=_repo("coredns/coredns", "v1.14.6"),
            new_tags=[
                clean_tag_finding("v1.14.7", summary="bugfix"),
                clean_tag_finding("v1.14.8", summary="bugfix"),
            ],
        ),
    ]

    finalize_run(repos, findings, settings)

    # processed.json is a snapshot of exactly what was reported.
    import json

    snapshot = json.loads(settings.state_path.read_text())["processed"]
    assert snapshot == {"coredns/coredns": ["v1.14.7", "v1.14.8"]}

    # current_upstream_tag advanced to the newest reported tag; others untouched.
    reloaded = {r.name: r for r in load_registry(settings.tracker_path)}
    assert reloaded["coredns/coredns"].current_upstream_tag == "v1.14.8"
    assert reloaded["etcd-io/etcd"].current_upstream_tag == "v3.6.13"


def test_finalize_quiet_run_is_noop(tmp_path: Path):
    settings = _settings(tmp_path)
    repos = [_repo("coredns/coredns", "v1.14.6")]
    finalize_run(repos, [], settings)
    assert not settings.state_path.exists()
    assert not settings.tracker_path.exists()
