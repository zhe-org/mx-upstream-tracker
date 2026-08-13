"""GitHub upstream discovery (Milestone 2 / 4).

Thin wrapper over the GitHub API (via PyGithub) for listing tags/releases and
fetching release notes. Authenticates with ``GH_TOKEN`` when available (public
repos are readable unauthenticated, but a token raises the rate limit).
"""

from __future__ import annotations

from github import Auth, Github, GithubException

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
    """Return the upstream release-notes body for ``tag`` (``""`` if none).

    Many tags have no GitHub *release* attached (only a git tag); that is not an
    error for the preflight gate, so a missing release degrades to an empty
    string rather than raising.
    """

    client = _client()
    try:
        release = client.get_repo(repo).get_release(tag)
    except GithubException:
        return ""
    finally:
        client.close()
    return release.body or ""


def compare_tags(repo: str, base: str, head: str) -> dict:
    """Summarise the ``base..head`` range: commit count, messages, changed files."""

    client = _client()
    try:
        comparison = client.get_repo(repo).compare(base, head)
        return {
            "total_commits": comparison.total_commits,
            "commit_messages": [c.commit.message for c in comparison.commits],
            "files": [f.filename for f in comparison.files],
        }
    finally:
        client.close()
