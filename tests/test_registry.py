"""Registry loader + validation tests (Milestone 1).

Covers the seed registry (``registries/upstream-tracker-1-36.yaml`` — parses
cleanly into all tracked repos, launchpad->github fork conversion held) and the
loader's failure modes (missing file, bad YAML, wrong shape, missing/extra
fields).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tracker import config
from tracker.models import RepoConfig
from tracker.registry import RegistryError, load_registry, save_registry

EXPECTED_REPO_COUNT = 15


def _valid_entry(**overrides) -> dict:
    entry = {
        "name": "coredns/coredns",
        "upstream": "https://github.com/coredns/coredns",
        "canonical_repo": "https://github.com/canonical/mx-coredns",
        "canonical_branch": "canonical/1.14-26.04/stable",
        "current_upstream_tag": "v1.14.6",
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


def test_seed_contains_kubernetes():
    repos = {r.name: r for r in load_registry(config.DEFAULT_TRACKER_PATH)}
    k8s = repos["kubernetes/kubernetes"]
    assert k8s.canonical_repo == "https://github.com/canonical/mx-kubernetes"
    assert k8s.current_upstream_tag == "v1.36.2"


# --- Valid custom registry ----------------------------------------------


def test_valid_registry_roundtrips(tmp_path: Path):
    import yaml

    path = _write(tmp_path / "t.yaml", yaml.safe_dump({"repos": [_valid_entry()]}))
    repos = load_registry(path)
    assert len(repos) == 1
    assert repos[0].name == "coredns/coredns"


def test_save_registry_roundtrips(tmp_path: Path):
    original = load_registry(config.DEFAULT_TRACKER_PATH)
    out = tmp_path / "out.yaml"
    save_registry(out, original)
    reloaded = load_registry(out)
    assert [r.model_dump() for r in reloaded] == [r.model_dump() for r in original]


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
