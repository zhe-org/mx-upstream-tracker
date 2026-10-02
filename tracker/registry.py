"""Upstream-tracker registry loader + fork baseline resolution.

The registry (one ``registries/upstream-tracker-<set>.yaml`` per K8s release
set) is the version-controlled input that drives the whole tracker: it lists,
per repo, the upstream we watch, our Canonical fork, and its release branch. The
release set is selected by the CLI argument.

The baseline — the newest upstream tag the fork branch has incorporated — is
*not* stored in the registry. Each fork branch records it in
``canonical/upstream-version``; :func:`resolve_baselines` reads that file at run
time so the tracker always diffs against what actually shipped, never against a
hand-maintained copy that drifts while releases are in flight.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from tracker.models import RepoConfig
from tracker.versioning import parse_ref

# File in every fork release branch holding the merged upstream tag.
UPSTREAM_VERSION_PATH = "canonical/upstream-version"

# canonical/<major>.<minor>/<risk>: the fork branch naming model.
_BRANCH_RE = re.compile(r"^canonical/(?P<major>\d+)\.(?P<minor>\d+)/[a-z]+$")
_GITHUB_PREFIX = "https://github.com/"


class RegistryError(ValueError):
    """Raised when the tracker registry is missing, malformed, or invalid."""


def load_registry(path: str | Path) -> list[RepoConfig]:
    """Load and validate the tracker registry at ``path``.

    Returns the list of validated :class:`RepoConfig` entries with an empty
    ``current_upstream_tag``; :func:`resolve_baselines` fills it in.

    Raises :class:`RegistryError` with a clear message when the file is
    missing, is not valid YAML, has the wrong top-level shape, contains no
    repos, or any entry fails schema validation.
    """

    path = Path(path)

    if not path.exists():
        raise RegistryError(
            f"Registry file not found: {path}. Create it (see "
            "registries/upstream-tracker-1-36.yaml) or select a valid release set."
        )

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RegistryError(f"Registry {path} is not valid YAML: {exc}") from exc

    if raw is None:
        raise RegistryError(f"Registry {path} is empty.")

    if not isinstance(raw, dict) or "repos" not in raw:
        raise RegistryError(f"Registry {path} must be a mapping with a top-level 'repos' key.")

    repos_raw = raw["repos"]
    if not isinstance(repos_raw, list) or not repos_raw:
        raise RegistryError(f"Registry {path}: 'repos' must be a non-empty list of repo entries.")

    repos: list[RepoConfig] = []
    for index, entry in enumerate(repos_raw):
        if not isinstance(entry, dict):
            raise RegistryError(
                f"Registry {path}: repos[{index}] must be a mapping, got {type(entry).__name__}."
            )
        if "current_upstream_tag" in entry:
            label = entry.get("name", f"index {index}")
            raise RegistryError(
                f"Registry {path}: '{label}' sets current_upstream_tag; the baseline now "
                f"comes from {UPSTREAM_VERSION_PATH} in the fork branch. Remove the key."
            )
        try:
            repos.append(RepoConfig(**entry))
        except ValidationError as exc:
            label = entry.get("name", f"index {index}")
            raise RegistryError(f"Registry {path}: invalid entry for '{label}':\n{exc}") from exc

    return repos


def fork_slug(repo: RepoConfig) -> str:
    """``https://github.com/canonical/mx-coredns`` -> ``canonical/mx-coredns``."""

    url = repo.canonical_repo.rstrip("/").removesuffix(".git")
    if not url.startswith(_GITHUB_PREFIX):
        raise RegistryError(f"{repo.name}: canonical_repo {repo.canonical_repo!r} is not GitHub")
    return url.removeprefix(_GITHUB_PREFIX)


def check_baseline(repo: RepoConfig, tag: str) -> None:
    """Fail loudly unless ``tag`` parses and sits on the branch's major.minor.

    ``canonical/2.0/stable`` must hold a ``2.0.x`` tag, else the run would watch
    a different line than the branch it merges into.
    """

    parsed = parse_ref(tag)
    if parsed is None:
        raise RegistryError(
            f"{repo.name}: {UPSTREAM_VERSION_PATH} on {repo.canonical_branch} holds "
            f"an unparseable tag {tag!r}."
        )
    branch = _BRANCH_RE.match(repo.canonical_branch)
    if branch is None:
        raise RegistryError(
            f"{repo.name}: branch {repo.canonical_branch!r} is not "
            "canonical/<major>.<minor>/<risk>."
        )
    branch_line = (int(branch["major"]), int(branch["minor"]))
    if branch_line != parsed.line[1:]:
        raise RegistryError(
            f"{repo.name}: {UPSTREAM_VERSION_PATH} on {repo.canonical_branch} holds {tag!r}, "
            "which is not on the branch's major.minor line."
        )


def resolve_baselines(repos: list[RepoConfig]) -> list[RepoConfig]:
    """Fill each repo's ``current_upstream_tag`` from its fork branch.

    Reads ``canonical/upstream-version`` from ``canonical_branch`` of the fork
    and validates it with :func:`check_baseline`. A missing or invalid file
    raises :class:`RegistryError` (the run must not guess a baseline).
    """

    # Imported here so offline registry loads never pull in the GitHub client.
    from tracker.tools.github import read_repo_file

    resolved: list[RepoConfig] = []
    for repo in repos:
        try:
            tag = read_repo_file(fork_slug(repo), UPSTREAM_VERSION_PATH, repo.canonical_branch)
        except FileNotFoundError as exc:
            raise RegistryError(
                f"{repo.name}: {UPSTREAM_VERSION_PATH} not found on "
                f"{repo.canonical_repo}@{repo.canonical_branch}."
            ) from exc
        tag = tag.strip()
        check_baseline(repo, tag)
        resolved.append(repo.model_copy(update={"current_upstream_tag": tag}))
    return resolved
