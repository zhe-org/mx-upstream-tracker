"""Orchestrator agent (Milestone 2).

Cheap, deterministic bookkeeping. Responsibilities that live here:
  - load the upstream tracker registry
  - discover new upstream tags matching each repo's tracked line (newer than
    ``current_upstream_tag``, which the finalize step advances so we never
    double-report)

Dispatch (fan-out to the per-repo sub-graph) lives in :mod:`tracker.graph`,
next to the sub-graph it invokes — this module has no knowledge of sub-graphs,
keeping the dependency flow one-way (``graph`` → ``orchestrator``).
"""

from __future__ import annotations

from tracker.config import load_settings
from tracker.models import GraphState, RepoJob
from tracker.tools.github import list_tags
from tracker.versioning import select_new_tags


def load_tracker_node(state: GraphState) -> GraphState:
    """Load and validate the upstream tracker registry into ``state['repos']``."""
    # Imported here to keep the registry dependency lazy and easy to patch.
    from tracker.registry import load_registry

    settings = load_settings()
    repos = load_registry(settings.tracker_path)
    return {"repos": repos}


def discover_releases_node(state: GraphState) -> GraphState:
    """Attach one :class:`RepoJob` per repo that has new tags.

    For each repo, list upstream tags and keep only those on the tracked line
    and newer than ``current_upstream_tag`` (:func:`select_new_tags`). Because
    the finalize step advances ``current_upstream_tag`` to the newest reported
    tag, this baseline alone guarantees we never re-surface an already-reported
    tag — no separate dedup store is needed. Repos with no new tags are omitted,
    so a quiet run yields an empty ``jobs`` list and no sub-graphs.
    """

    jobs: list[RepoJob] = []
    for repo in state.get("repos", []):
        candidates = list_tags(repo.name)
        new_tags = select_new_tags(repo.current_upstream_tag, candidates)
        if new_tags:
            jobs.append(RepoJob(repo=repo, new_tags=new_tags))
    return {"jobs": jobs}
