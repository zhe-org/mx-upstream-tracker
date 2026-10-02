"""Central configuration and secrets handling.

All runtime configuration is sourced from environment variables (loaded from a
local ``.env`` during development via ``python-dotenv``; injected as GitHub
Actions secrets in CI). Nothing secret is ever committed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env if present (no-op in CI where real env vars are already set).
load_dotenv()

# Repository root (this file lives at tracker/config.py, so go up two levels).
ROOT_DIR = Path(__file__).resolve().parent.parent

# Per-release-set inputs/outputs. A single run targets one K8s release set (e.g.
# "1.36"): its registry lives at ``registries/upstream-tracker-1-36.yaml``, its
# last-scanned watermark at ``state/processed-1-36.json`` and its 7-day report
# at ``reports/report-1-36.json``. The release set is chosen by the CLI argument
# (``python -m tracker.main 1.36``), which sets the ``RELEASE_SET`` env var that
# every ``load_settings()`` call then reads.
REGISTRIES_DIR = ROOT_DIR / "registries"
STATE_DIR = ROOT_DIR / "state"
REPORTS_DIR = ROOT_DIR / "reports"
DEFAULT_ARTIFACT_DIR = ROOT_DIR / "artifacts"

# Fallback release set when ``RELEASE_SET`` is unset (real runs always set it via
# the CLI arg; this keeps internal/test ``load_settings()`` calls sane).
DEFAULT_RELEASE_SET = "1.36"


def slug_for(release_set: str) -> str:
    """Filename slug for a release set: dot form in, dash form out (``1.36`` -> ``1-36``)."""
    return release_set.replace(".", "-")


def registry_path_for(release_set: str) -> Path:
    """Registry path for ``release_set`` (dot form), e.g. ``upstream-tracker-1-36.yaml``."""
    return REGISTRIES_DIR / f"upstream-tracker-{slug_for(release_set)}.yaml"


def state_path_for(release_set: str) -> Path:
    """Last-scanned watermark path for ``release_set``, e.g. ``state/processed-1-36.json``."""
    return STATE_DIR / f"processed-{slug_for(release_set)}.json"


def report_filename(release_set: str) -> str:
    """Report file name for ``release_set``, e.g. ``report-1-36.json``."""
    return f"report-{slug_for(release_set)}.json"


def reports_path_for(release_set: str) -> Path:
    """7-day report path for ``release_set``, e.g. ``reports/report-1-36.json``."""
    return REPORTS_DIR / report_filename(release_set)


# Default on-disk locations (for the fallback release set).
DEFAULT_TRACKER_PATH = registry_path_for(DEFAULT_RELEASE_SET)
DEFAULT_STATE_PATH = state_path_for(DEFAULT_RELEASE_SET)


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings for a single tracker run."""

    # LLM: OpenRouter exposes an OpenAI-compatible chat completions API. We
    # authenticate with OPENROUTER_API_KEY and target google/gemini-2.5-pro.
    # GH_TOKEN is still used, but only for the GitHub REST API + fork clone.
    gh_token: str | None
    openrouter_api_key: str | None
    llm_base_url: str
    llm_model: str
    llm_temperature: float

    # Paths.
    tracker_path: Path
    state_path: Path
    reports_path: Path
    artifact_dir: Path

    # Max concurrent repo sub-graphs during dispatch. ``None`` => derive from the
    # machine's CPU count. Each job is I/O-bound (GitHub / git / LLM), so this
    # bounds heavy work (trial merges) rather than saturating cores.
    dispatch_max_workers: int | None

    def require_gh_token(self) -> str:
        if not self.gh_token:
            raise RuntimeError(
                "GH_TOKEN is not set. Export it locally (see .env.example) or "
                "provide it as a GitHub Actions secret."
            )
        return self.gh_token

    def require_openrouter_key(self) -> str:
        if not self.openrouter_api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Export it locally (see .env.example) "
                "or provide it as a GitHub Actions secret."
            )
        return self.openrouter_api_key


def load_settings() -> Settings:
    """Build :class:`Settings` from the current environment.

    The tracker/state/reports paths are derived from the ``RELEASE_SET`` env var
    (dot form, e.g. ``1.36``), which ``main`` sets from the CLI argument. Explicit
    ``TRACKER_PATH`` / ``STATE_PATH`` / ``REPORTS_PATH`` still override (tests).
    """

    release_set = os.getenv("RELEASE_SET", DEFAULT_RELEASE_SET)
    tracker_default = registry_path_for(release_set)
    state_default = state_path_for(release_set)

    return Settings(
        gh_token=os.getenv("GH_TOKEN"),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY"),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1"),
        llm_model=os.getenv("LLM_MODEL", "google/gemini-2.5-pro"),
        llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.0")),
        tracker_path=Path(os.getenv("TRACKER_PATH", str(tracker_default))),
        state_path=Path(os.getenv("STATE_PATH", str(state_default))),
        reports_path=Path(os.getenv("REPORTS_PATH", str(reports_path_for(release_set)))),
        artifact_dir=Path(os.getenv("ARTIFACT_DIR", str(DEFAULT_ARTIFACT_DIR))),
        dispatch_max_workers=_optional_int(os.getenv("DISPATCH_MAX_WORKERS")),
    )


def _optional_int(value: str | None) -> int | None:
    """Parse an optional positive int env var; blank/invalid/<=0 => ``None``."""
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None
