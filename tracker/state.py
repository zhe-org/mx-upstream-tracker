"""Processed-tag state persistence.

We persist which upstream tags have already been reported so runs never
double-report. v1 uses a flat JSON file committed to the repo (simple, easy to
audit in git history). The shape is intentionally minimal; richer per-run
history can be layered on later without breaking this format.

    {
      "processed": {
        "kubernetes/kubernetes": ["v1.36.5", "v1.36.6"],
        "coredns/coredns": ["v1.11.3"]
      }
    }
"""

from __future__ import annotations

import json
from pathlib import Path

from tracker.config import load_settings


def _state_path(path: Path | None = None) -> Path:
    return path or load_settings().state_path


def load_processed(path: Path | None = None) -> dict[str, list[str]]:
    """Load the mapping of ``repo -> [processed tags]``."""

    p = _state_path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text())
    return data.get("processed", {})


def is_processed(repo: str, tag: str, path: Path | None = None) -> bool:
    return tag in load_processed(path).get(repo, [])


def mark_processed(repo: str, tags: list[str], path: Path | None = None) -> None:
    """Record ``tags`` as processed for ``repo`` and persist to disk."""

    p = _state_path(path)
    processed = load_processed(p)
    existing = set(processed.get(repo, []))
    existing.update(tags)
    processed[repo] = sorted(existing)

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"processed": processed}, indent=2, sort_keys=True) + "\n")
