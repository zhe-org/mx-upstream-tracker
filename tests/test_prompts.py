"""Prompt loader tests (Milestone 5).

Agent prompts are plain ``.txt`` files loaded at runtime so they can be edited
without code changes. These pin the loader contract and that the analyzer's
system prompt carries the project knowledge the agent needs.
"""

from __future__ import annotations

import pytest

from tracker.prompts import load_prompt


def test_loads_analyzer_system_prompt():
    text = load_prompt("analyzer_system")
    assert text  # non-empty
    assert not text.startswith("\n")  # stripped


def test_analyzer_prompt_carries_project_knowledge():
    text = load_prompt("analyzer_system").lower()
    # Key domain concepts the agent must know about.
    assert "mixed sources" in text
    assert "superdistro" in text
    assert "vendor" in text
    # The specific rule from the project overview: CI/workflow conflicts are
    # trivial and should just be dropped.
    assert ".github/workflows" in text
    assert "vmware" in text


def test_missing_prompt_raises():
    with pytest.raises(FileNotFoundError):
        load_prompt("does_not_exist")
