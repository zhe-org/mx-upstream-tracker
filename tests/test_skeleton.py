"""Skeleton smoke tests (M0).

These verify the scaffolding is wired correctly: config loads and the graph
compiles. Agent node bodies are tested with their milestones.
"""

from __future__ import annotations

from tracker import config
from tracker.graph import build_repo_subgraph


def test_settings_load_with_defaults(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    settings = config.load_settings()
    assert settings.llm_model == "google/gemini-2.5-pro"
    assert settings.llm_base_url == "https://openrouter.ai/api/v1"


def test_subgraph_compiles():
    # Building must succeed even before node bodies are implemented.
    assert build_repo_subgraph() is not None
