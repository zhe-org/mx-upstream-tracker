"""GitHub upstream discovery (Milestone 2 / 4).

Thin wrapper over the GitHub API (via PyGithub) for listing tags/releases and
fetching release notes. Authenticates with ``GH_TOKEN`` when available (public
repos are readable unauthenticated, but a token raises the rate limit).
"""

from __future__ import annotations

from github import Auth, Github

from tracker.config import load_settings

_PER_PAGE = 100  # fewer round-trips when enumerating tag-heavy repos (e.g. k8s)


def _client() -> Github:
    token = load_settings().gh_token
    auth = Auth.Token(token) if token else None
    return Github(auth=auth, per_page=_PER_PAGE)


def list_tags(repo: str) -> list[str]:
    """Return upstream tag names for ``owner/name`` (GitHub's order, unsorted).

    Callers filter/sort via :mod:`tracker.versioning`; this just enumerates all
    tags across PyGithub's paginated results.
    """

    client = _client()
    try:
        repository = client.get_repo(repo)
        return [tag.name for tag in repository.get_tags()]
    finally:
        client.close()


def get_release_notes(repo: str, tag: str) -> str:
    """Return the upstream release notes body for ``tag``."""
    raise NotImplementedError("Milestone 4: fetch release notes")


def compare_tags(repo: str, base: str, head: str) -> dict:
    """Return the commit log / changed files between two tags."""
    raise NotImplementedError("Milestone 4: tag-to-tag comparison")
