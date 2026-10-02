"""Registry loader + fork baseline tests.

Covers the seed registry (``registries/upstream-tracker-1-36.yaml`` — parses
cleanly into all tracked repos), the loader's failure modes (missing file, bad
YAML, wrong shape, missing/extra fields), and baseline resolution from the
fork's ``canonical/upstream-version``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tracker import config, registry
from tracker.models import RepoConfig
from tracker.registry import RegistryError, load_registry, resolve_baselines

EXPECTED_REPO_COUNT = 15


def _valid_entry(**overrides) -> dict:
    entry = {
        "name": "coredns/coredns",
        "upstream": "https://github.com/coredns/coredns",
        "canonical_repo": "https://github.com/canonical/mx-coredns",
        "canonical_branch": "canonical/1.14/stable",
    }
    entry.update(overrides)
    return entry


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


# --- The seed registry ---------------------------------------------------


def test_seed_registry_parses():
    repos = load_registry(config.DEFAULT_TRACKER_PATH)
    assert len(repos) == EXPECTED_REPO_COUNT
    assert all(isinstance(r, RepoConfig) for r in repos)


def test_seed_forks_are_canonical_github():
    repos = load_registry(config.DEFAULT_TRACKER_PATH)
    for r in repos:
        assert r.canonical_repo.startswith("https://github.com/canonical/"), r.name
        assert r.upstream.startswith("https://github.com/"), r.name


# --- Valid custom registry ----------------------------------------------


def test_valid_registry_roundtrips(tmp_path: Path):
    import yaml

    path = _write(tmp_path / "t.yaml", yaml.safe_dump({"repos": [_valid_entry()]}))
    repos = load_registry(path)
    assert len(repos) == 1
    assert repos[0].name == "coredns/coredns"


# --- Failure modes -------------------------------------------------------


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(RegistryError, match="not found"):
        load_registry(tmp_path / "nope.yaml")


def test_invalid_yaml_raises(tmp_path: Path):
    path = _write(tmp_path / "bad.yaml", "repos: [: :\n")
    with pytest.raises(RegistryError, match="not valid YAML"):
        load_registry(path)


def test_empty_file_raises(tmp_path: Path):
    path = _write(tmp_path / "empty.yaml", "")
    with pytest.raises(RegistryError, match="empty"):
        load_registry(path)


def test_missing_repos_key_raises(tmp_path: Path):
    path = _write(tmp_path / "x.yaml", "other: 1\n")
    with pytest.raises(RegistryError, match="top-level 'repos'"):
        load_registry(path)


def test_repos_not_a_list_raises(tmp_path: Path):
    path = _write(tmp_path / "x.yaml", "repos: {}\n")
    with pytest.raises(RegistryError, match="non-empty list"):
        load_registry(path)


def test_empty_repos_list_raises(tmp_path: Path):
    path = _write(tmp_path / "x.yaml", "repos: []\n")
    with pytest.raises(RegistryError, match="non-empty list"):
        load_registry(path)


def test_missing_required_field_raises(tmp_path: Path):
    import yaml

    entry = _valid_entry()
    del entry["canonical_branch"]
    path = _write(tmp_path / "x.yaml", yaml.safe_dump({"repos": [entry]}))
    with pytest.raises(RegistryError, match="canonical_branch"):
        load_registry(path)


def test_unknown_field_rejected(tmp_path: Path):
    import yaml

    path = _write(tmp_path / "x.yaml", yaml.safe_dump({"repos": [_valid_entry(typo_field="x")]}))
    with pytest.raises(RegistryError, match="coredns/coredns"):
        load_registry(path)


def test_registry_baseline_key_rejected(tmp_path: Path):
    # The baseline moved to the fork branch; a stale key must not be silently used.
    import yaml

    entry = _valid_entry(current_upstream_tag="v1.14.6")
    path = _write(tmp_path / "x.yaml", yaml.safe_dump({"repos": [entry]}))
    with pytest.raises(RegistryError, match="upstream-version"):
        load_registry(path)


# --- Baseline resolution -------------------------------------------------


def _stub_fork_file(monkeypatch, files: dict[tuple[str, str], str]) -> None:
    from tracker.tools import github

    def fake_read(repo: str, path: str, ref: str) -> str:
        assert path == registry.UPSTREAM_VERSION_PATH
        try:
            return files[(repo, ref)]
        except KeyError:
            raise FileNotFoundError(f"{repo}@{ref}:{path}") from None

    monkeypatch.setattr(github, "read_repo_file", fake_read)


def test_resolve_reads_fork_branch_file(monkeypatch):
    _stub_fork_file(monkeypatch, {("canonical/mx-coredns", "canonical/1.14/stable"): "v1.14.6\n"})
    (repo,) = resolve_baselines([RepoConfig(**_valid_entry())])
    assert repo.current_upstream_tag == "v1.14.6"


def test_resolve_accepts_prerelease_on_edge_branch(monkeypatch):
    entry = _valid_entry(canonical_branch="canonical/1.15/edge")
    _stub_fork_file(monkeypatch, {("canonical/mx-coredns", "canonical/1.15/edge"): "v1.15.0-rc.1"})
    (repo,) = resolve_baselines([RepoConfig(**entry)])
    assert repo.current_upstream_tag == "v1.15.0-rc.1"


def test_resolve_missing_file_raises(monkeypatch):
    _stub_fork_file(monkeypatch, {})
    with pytest.raises(RegistryError, match="not found"):
        resolve_baselines([RepoConfig(**_valid_entry())])


def test_resolve_tag_off_branch_line_raises(monkeypatch):
    # canonical/1.14/stable holding a 1.13 tag would watch the wrong line.
    _stub_fork_file(monkeypatch, {("canonical/mx-coredns", "canonical/1.14/stable"): "v1.13.9"})
    with pytest.raises(RegistryError, match="major.minor"):
        resolve_baselines([RepoConfig(**_valid_entry())])


def test_resolve_unparseable_tag_raises(monkeypatch):
    _stub_fork_file(monkeypatch, {("canonical/mx-coredns", "canonical/1.14/stable"): "latest"})
    with pytest.raises(RegistryError, match="unparseable"):
        resolve_baselines([RepoConfig(**_valid_entry())])
