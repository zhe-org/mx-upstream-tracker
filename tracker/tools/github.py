"""GitHub upstream discovery (Milestone 2 / 4).

Thin wrapper over the GitHub REST API for listing tags/releases and fetching
release notes. Authenticates with ``GH_TOKEN``. Skeleton only.
"""

from __future__ import annotations


def list_tags(repo: str) -> list[str]:
    """Return upstream tags for ``owner/name``, newest first."""
    raise NotImplementedError("Milestone 2: list tags via GitHub API")


def get_release_notes(repo: str, tag: str) -> str:
    """Return the upstream release notes body for ``tag``."""
    raise NotImplementedError("Milestone 4: fetch release notes")


def compare_tags(repo: str, base: str, head: str) -> dict:
    """Return the commit log / changed files between two tags."""
    raise NotImplementedError("Milestone 4: tag-to-tag comparison")
