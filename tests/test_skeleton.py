"""Skeleton smoke tests (M0).

These verify the scaffolding is wired correctly: config loads, the graph
compiles, and the processed-tag state store round-trips. Agent node bodies are
tested with their milestones.
"""

from __future__ import annotations

from pathlib import Path

from tracker import config, state
from tracker.graph import build_graph, build_repo_subgraph


def test_settings_load_with_defaults(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    settings = config.load_settings()
    assert settings.llm_model == "gemini-2.5-pro"
    assert settings.llm_base_url == "https://api.githubcopilot.com"


def test_graph_compiles():
    # Building must succeed even before node bodies are implemented.
    assert build_graph() is not None
    assert build_repo_subgraph() is not None


def test_state_roundtrip(tmp_path: Path):
    p = tmp_path / "processed.json"
    assert state.is_processed("kubernetes/kubernetes", "v1.36.6", p) is False

    state.mark_processed("kubernetes/kubernetes", ["v1.36.5", "v1.36.6"], p)
    assert state.is_processed("kubernetes/kubernetes", "v1.36.6", p) is True

    # Idempotent + additive.
    state.mark_processed("kubernetes/kubernetes", ["v1.36.6", "v1.36.7"], p)
    assert state.load_processed(p)["kubernetes/kubernetes"] == [
        "v1.36.5",
        "v1.36.6",
        "v1.36.7",
    ]
