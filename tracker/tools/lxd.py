"""Ephemeral LXD container lifecycle for trial merges (Milestone 4).

Spin up a throwaway container, run the trial merge inside it, capture output,
then destroy it. Trial merges never touch the real fork. Skeleton only.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def ephemeral_container(image: str = "ubuntu:24.04", remote: str = "local") -> Iterator[str]:
    """Yield a container name; guarantee teardown on exit."""
    raise NotImplementedError("Milestone 4: launch + destroy ephemeral LXD container")
    yield ""  # pragma: no cover - documents the contract


def exec_in(container: str, argv: list[str]) -> dict:
    """Run a command in the container; return ``{rc, stdout, stderr}``."""
    raise NotImplementedError("Milestone 4: exec inside container")
