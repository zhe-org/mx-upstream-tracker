"""Local git operations against our forks (Milestone 4).

Used inside the ephemeral LXD container to check out a fork and attempt a
trial merge of a new upstream tag. Skeleton only.
"""

from __future__ import annotations


def clone_fork(fork_url: str, dest: str) -> None:
    raise NotImplementedError("Milestone 4: clone fork into container")


def trial_merge(dest: str, upstream_url: str, tag: str) -> dict:
    """Attempt merging ``tag`` from upstream; capture (never raise on conflict).

    Returns a structured result: ``{result, conflicting_paths, output}`` where
    ``result`` is one of ``clean | conflict | error``.
    """
    raise NotImplementedError("Milestone 4: capture trial-merge output")
