"""Analyzer agent tests (Milestone 5).

The LLM node body is a follow-up; for now this pins that the agent loads its
project-knowledge system prompt at runtime and that the passthrough node is
inert (leaves the preflight finding unchanged).
"""

from __future__ import annotations

from tracker.agents import analyzer


def test_system_prompt_loaded_at_runtime():
    prompt = analyzer.system_prompt()
    assert "mixed sources" in prompt.lower()
    assert "superdistro" in prompt.lower()


def test_analyzer_node_is_passthrough():
    # Until the LLM body lands, the node must not alter state.
    assert analyzer.analyzer_node({"repo": None, "tag": "v1", "tag_finding": None}) == {}
