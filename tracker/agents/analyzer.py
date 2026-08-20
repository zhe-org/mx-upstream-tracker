"""Analyzer agent (Milestone 5).

Only runs when preflight flags something. Consumes the focused handoff bundle
(:class:`~tracker.models.EvidenceBundle`, not the full raw diff) and produces the
deep analysis:
  - CVEs: what, severity, affected component/code path in our fork
  - merge conflicts: which files, why, resolution hints
  - behaviour changes / deprecations: what changed, blast radius, verify what
  - concrete reviewer guidance

The agent's project knowledge (mixed sources, the Canonical branching model,
Superdistro dependency policy, the "CI/workflow conflicts are trivial" rule) is
kept out of the code in ``tracker/prompts/analyzer_system.txt`` and loaded at
runtime via :func:`system_prompt`, so it can be tuned without code changes. The
dynamic evidence is rendered per tag and sent as the human message.

The LLM returns an :class:`~tracker.models.AnalyzerOutput` (structured output);
we merge it into the flagged finding. The analyzer may **escalate** risk (e.g. a
high-severity CVE) but never downgrades below the preflight risk. Any model /
parsing failure degrades gracefully: the finding stays flagged and a note
records that deep analysis was unavailable, so one tag never aborts the run.
"""

from __future__ import annotations

from tracker.models import (
    Analysis,
    AnalyzerOutput,
    EvidenceBundle,
    NewTagFinding,
    RepoConfig,
    Risk,
    SubgraphState,
)
from tracker.prompts import load_prompt
from tracker.tools.llm import get_chat_model

_SYSTEM_PROMPT_NAME = "analyzer_system"
_RANK: dict[Risk, int] = {"low": 0, "medium": 1, "high": 2}

# Bound the evidence we send so a huge release note / diff cannot blow the
# context window; the tail of a giant blob is rarely the signal.
_NOTES_CAP = 8000
_HUNKS_CAP = 8000
_COMMITS_CAP = 100
_FILES_CAP = 100


def system_prompt() -> str:
    """Load the analyzer's system prompt (project knowledge) at runtime."""
    return load_prompt(_SYSTEM_PROMPT_NAME)


def _max_risk(a: Risk, b: Risk) -> Risk:
    """Higher of two risks (analyzer may escalate, never downgrade)."""
    return a if _RANK[a] >= _RANK[b] else b


def _evidence_text(repo: RepoConfig, tag: str, finding: NewTagFinding, ev: EvidenceBundle) -> str:
    """Render the per-tag handoff bundle as the human message for the LLM."""

    trial = finding.preflight.trial_merge
    lines = [
        f"Component (fork): {repo.name}",
        f"Fork branch: {repo.canonical_branch}",
        f"Last merged upstream tag: {repo.current_upstream_tag}",
        f"New upstream tag under review: {tag}",
        "",
        f"Preflight decision: {finding.preflight.decision} (risk {finding.risk})",
        f"Trial merge result: {trial.result}",
        f"Conflicting paths: {trial.conflicting_paths}",
        f"CVE references detected: {ev.cve_refs}",
        f"Changed files: {ev.changed_files[:_FILES_CAP]}",
        "",
        "Commit messages:",
        "\n".join(f"- {m.strip()}" for m in ev.commit_messages[:_COMMITS_CAP]) or "(none)",
        "",
        "Release notes:",
        ev.release_notes[:_NOTES_CAP] or "(none)",
        "",
        "Trial-merge conflict hunks:",
        ev.conflict_hunks[:_HUNKS_CAP] or "(none)",
    ]
    return "\n".join(lines)


def analyze_tag(
    repo: RepoConfig,
    tag: str,
    finding: NewTagFinding,
    evidence: EvidenceBundle,
    *,
    model=None,
) -> NewTagFinding:
    """Deep-dive one flagged tag and return an enriched finding.

    ``model`` is injectable for tests; in production it defaults to the Copilot /
    gemini chat model. On any failure the original flagged finding is returned
    with a note appended (graceful degradation).
    """

    model = model or get_chat_model()
    messages = [
        ("system", system_prompt()),
        ("human", _evidence_text(repo, tag, finding, evidence)),
    ]

    try:
        out: AnalyzerOutput = model.with_structured_output(AnalyzerOutput).invoke(messages)
    except Exception as exc:  # never abort the whole run for one tag
        note = finding.notes_for_reviewer or ""
        appended = f"{note}\n[deep analysis unavailable: {exc}]".strip()
        return finding.model_copy(update={"notes_for_reviewer": appended})

    return finding.model_copy(
        update={
            "risk": _max_risk(finding.risk, out.risk),
            "highlights": out.highlights,
            "dependencies": out.dependencies,
            "analysis": Analysis(cves=out.cves, conflicts=out.conflicts),
            "notes_for_reviewer": out.notes_for_reviewer or finding.notes_for_reviewer,
        }
    )


def analyzer_node(state: SubgraphState) -> SubgraphState:
    """Enrich the flagged ``tag_finding`` in place using the evidence bundle."""
    finding = analyze_tag(state["repo"], state["tag"], state["tag_finding"], state["evidence"])
    return {"tag_finding": finding}
