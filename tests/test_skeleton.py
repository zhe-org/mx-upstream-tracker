"""Skeleton smoke tests (M0).

These verify the scaffolding is wired correctly: config loads, the graph
compiles, and the processed-tag state store round-trips. Agent node bodies are
tested with their milestones.
"""

from __future__ import annotations

from pathlib import Path

from tracker import config, state
from tracker.graph import build_repo_subgraph


def test_settings_load_with_defaults(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    settings = config.load_settings()
    assert settings.llm_model == "gemini-2.5-pro"
    assert settings.llm_base_url == "https://api.githubcopilot.com"


def test_subgraph_compiles():
    # Building must succeed even before node bodies are implemented.
    assert build_repo_subgraph() is not None


def test_state_snapshot_overwrites(tmp_path: Path):
    import json

    p = tmp_path / "processed.json"

    state.save_processed({"kubernetes/kubernetes": ["v1.36.3"]}, p)
    assert json.loads(p.read_text())["processed"] == {"kubernetes/kubernetes": ["v1.36.3"]}

    # save_processed overwrites (last-run snapshot), it does not accumulate.
    state.save_processed({"coredns/coredns": ["v1.14.7"]}, p)
    assert json.loads(p.read_text())["processed"] == {"coredns/coredns": ["v1.14.7"]}
