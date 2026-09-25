"""Invariants over the registries we actually ship (``registries/*.yaml``).

``test_registry.py`` pins the *loader*; this module pins the *data*. Every
release set in the nightly matrix is driven by one of these files, and a typo in
any of them only surfaces at 09:00 UTC (a failed matrix job, or worse, a run
that silently reports nothing). These tests make that a CI failure instead:

  - every shipped registry parses and validates,
  - upstream/fork URLs follow the project's naming convention,
  - ``current_upstream_tag`` is parseable and sits on the track its
    ``canonical_branch`` names (a mismatch means the run would watch the wrong
    line or blow up in ``select_new_tags``),
  - no fork branch is tracked by two release sets (a component/branch belongs to
    exactly one set — the newest set that uses it — so shared components are not
    reviewed and bumped twice),
  - the nightly workflow's matrix matches the registries on disk.
"""

from __future__ import annotations

import re

import pytest
import yaml

from tracker import config, main
from tracker.models import RepoConfig
from tracker.registry import load_registry
from tracker.versioning import parse_ref

NIGHTLY_WORKFLOW = config.ROOT_DIR / ".github" / "workflows" / "nightly.yml"

# Release sets that ship a registry, discovered the same way the CLI does.
RELEASE_SETS = main._available_release_sets()

# canonical/<major>.<minor>/<risk> — the fork branch naming model. <risk> is
# edge < beta < candidate < stable; only stable is security-maintained.
BRANCH_RE = re.compile(r"^canonical/(?P<track>\d+\.\d+)/(?P<risk>edge|beta|candidate|stable)$")


def _repos(release_set: str) -> list[RepoConfig]:
    return load_registry(config.registry_path_for(release_set))


def _track(tag: str) -> str:
    """``v1.33.13`` -> ``1.33``; ``cluster-autoscaler-1.33.6`` -> ``1.33``."""
    parsed = parse_ref(tag)
    assert parsed is not None, f"unparseable tag: {tag!r}"
    return f"{parsed.release[0]}.{parsed.release[1]}"


# --- The registries exist ------------------------------------------------


def test_registries_are_shipped():
    # Guards the parametrized tests below: an empty glob would make them vacuous.
    assert RELEASE_SETS, f"no upstream-tracker-*.yaml found in {config.REGISTRIES_DIR}"


# --- Per-set invariants --------------------------------------------------


@pytest.mark.parametrize("release_set", RELEASE_SETS)
def test_shipped_registry_loads(release_set: str):
    repos = _repos(release_set)
    assert repos, f"{release_set}: registry has no repos"


@pytest.mark.parametrize("release_set", RELEASE_SETS)
def test_shipped_urls_follow_convention(release_set: str):
    for repo in _repos(release_set):
        assert repo.upstream == f"https://github.com/{repo.name}", (
            f"{release_set}/{repo.name}: upstream URL does not match the repo name"
        )
        assert repo.canonical_repo.startswith("https://github.com/canonical/mx-"), (
            f"{release_set}/{repo.name}: fork is not a canonical/mx-* GitHub repo"
        )


@pytest.mark.parametrize("release_set", RELEASE_SETS)
def test_shipped_tags_are_parseable(release_set: str):
    # select_new_tags() raises on an unparseable current_upstream_tag, which
    # would fail the whole nightly run for that set.
    for repo in _repos(release_set):
        assert parse_ref(repo.current_upstream_tag) is not None, (
            f"{release_set}/{repo.name}: unparseable current_upstream_tag "
            f"{repo.current_upstream_tag!r}"
        )


@pytest.mark.parametrize("release_set", RELEASE_SETS)
def test_shipped_branches_follow_naming_model(release_set: str):
    for repo in _repos(release_set):
        assert BRANCH_RE.match(repo.canonical_branch), (
            f"{release_set}/{repo.name}: branch {repo.canonical_branch!r} is not "
            "canonical/<major>.<minor>/<risk>"
        )


@pytest.mark.parametrize("release_set", RELEASE_SETS)
def test_branch_track_matches_current_tag(release_set: str):
    # canonical/2.0/stable must be pinned to a 2.0.x tag, else the run watches a
    # different line than the branch it would merge into.
    for repo in _repos(release_set):
        match = BRANCH_RE.match(repo.canonical_branch)
        assert match is not None, f"{release_set}/{repo.name}: {repo.canonical_branch!r}"
        assert match["track"] == _track(repo.current_upstream_tag), (
            f"{release_set}/{repo.name}: branch {repo.canonical_branch!r} does not "
            f"match tag {repo.current_upstream_tag!r}"
        )


# --- Cross-set invariants ------------------------------------------------


def test_no_fork_branch_tracked_by_two_release_sets():
    """A (component, fork branch) pair belongs to exactly one release set.

    Release sets share components whose track did not move (e.g. coredns 1.12
    across two K8s lines). We keep such a component only in the newest set that
    uses it, so the same branch is not trial-merged, reported, and bumped twice
    in one night.
    """

    owner: dict[tuple[str, str], str] = {}
    duplicates: list[str] = []
    for release_set in RELEASE_SETS:
        for repo in _repos(release_set):
            key = (repo.name, repo.canonical_branch)
            if key in owner:
                duplicates.append(
                    f"{repo.name} @ {repo.canonical_branch} in {owner[key]} and {release_set}"
                )
            else:
                owner[key] = release_set
    assert not duplicates, "fork branch tracked by more than one release set: " + "; ".join(
        duplicates
    )


def test_nightly_matrix_matches_shipped_registries():
    workflow = yaml.safe_load(NIGHTLY_WORKFLOW.read_text(encoding="utf-8"))
    matrix = workflow["jobs"]["run"]["strategy"]["matrix"]["release_set"]
    assert sorted(str(s) for s in matrix) == sorted(RELEASE_SETS), (
        "nightly.yml matrix and registries/ are out of sync"
    )
