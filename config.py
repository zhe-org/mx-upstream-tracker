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

# Repository root (this file lives at the project root).
ROOT_DIR = Path(__file__).resolve().parent

# Default on-disk locations.
DEFAULT_REGISTRY_PATH = ROOT_DIR / "registry.yaml"
DEFAULT_STATE_PATH = ROOT_DIR / "state" / "processed.json"
DEFAULT_ARTIFACT_DIR = ROOT_DIR / "artifacts"


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings for a single tracker run."""

    # LLM: GitHub Copilot exposes an OpenAI-compatible chat completions API.
    # We authenticate with GH_TOKEN and target gemini-2.5-pro.
    gh_token: str | None
    llm_base_url: str
    llm_model: str
    llm_temperature: float

    # Paths.
    registry_path: Path
    state_path: Path
    artifact_dir: Path

    # LXD host used for ephemeral trial-merge containers (M4).
    lxd_remote: str

    def require_gh_token(self) -> str:
        if not self.gh_token:
            raise RuntimeError(
                "GH_TOKEN is not set. Export it locally (see .env.example) or "
                "provide it as a GitHub Actions secret."
            )
        return self.gh_token


def load_settings() -> Settings:
    """Build :class:`Settings` from the current environment."""

    return Settings(
        gh_token=os.getenv("GH_TOKEN"),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.githubcopilot.com"),
        llm_model=os.getenv("LLM_MODEL", "gemini-2.5-pro"),
        llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.0")),
        registry_path=Path(os.getenv("REGISTRY_PATH", str(DEFAULT_REGISTRY_PATH))),
        state_path=Path(os.getenv("STATE_PATH", str(DEFAULT_STATE_PATH))),
        artifact_dir=Path(os.getenv("ARTIFACT_DIR", str(DEFAULT_ARTIFACT_DIR))),
        lxd_remote=os.getenv("LXD_REMOTE", "local"),
    )
