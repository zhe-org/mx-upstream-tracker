"""Processed-tag record.

A run overwrites ``processed.json`` with the tags it just reported. This is a
**human-readable record of the last run** (handy alongside the report) — it is
*not* used for dedup. Uniqueness is guaranteed by ``upstream-tracker.yaml``:
the finalize step advances ``current_upstream_tag`` to the newest reported tag,
so the next run's discovery (``select_new_tags``) never re-surfaces it.

v1 uses a flat JSON file committed to the repo (simple, easy to audit in git
history). Shape:

    {
      "processed": {
        "kubernetes/kubernetes": ["v1.36.3"],
        "coredns/coredns": ["v1.14.7"]
      }
    }
"""

from __future__ import annotations

import json
from pathlib import Path

from tracker.config import load_settings


def _state_path(path: Path | None = None) -> Path:
    return path or load_settings().state_path


def save_processed(processed: dict[str, list[str]], path: Path | None = None) -> None:
    """Overwrite the record with ``processed`` (``repo -> [tags]``).

    Replaces the file wholesale (last-run snapshot), sorting keys and tags for a
    stable, diff-friendly on-disk form.
    """

    p = _state_path(path)
    normalized = {repo: sorted(set(tags)) for repo, tags in processed.items()}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"processed": normalized}, indent=2, sort_keys=True) + "\n")
