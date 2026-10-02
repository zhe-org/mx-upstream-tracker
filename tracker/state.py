"""Last-scanned tag per repo — the discovery watermark.

Each release set keeps ``state/processed-<set>.json`` recording, per repo, the
newest upstream tag the tracker has already reported. Discovery only surfaces
tags newer than *both* the fork's baseline (``canonical/upstream-version``) and
this watermark, so a tag is reported once even while the fork has not merged it
yet. Shape::

    {
      "last_scanned": {
        "containerd/containerd": "v2.3.6",
        "kubernetes/kubernetes": "v1.36.6"
      }
    }

The file is merged, not overwritten: repos with no new tags keep their entry.
"""

from __future__ import annotations

import json
from pathlib import Path

from tracker.config import load_settings


def _state_path(path: Path | None = None) -> Path:
    return path or load_settings().state_path


def load_scanned(path: Path | None = None) -> dict[str, str]:
    """Return ``repo -> last scanned tag`` (empty when the file does not exist)."""

    p = _state_path(path)
    if not p.exists():
        return {}
    return dict(json.loads(p.read_text(encoding="utf-8")).get("last_scanned", {}))


def save_scanned(scanned: dict[str, str], path: Path | None = None) -> None:
    """Write ``repo -> last scanned tag`` with sorted keys (stable diffs)."""

    p = _state_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"last_scanned": scanned}, indent=2, sort_keys=True) + "\n")
