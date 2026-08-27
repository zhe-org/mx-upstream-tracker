"""Analyzer agent tests (Milestone 5).

The analyzer feeds the preflight handoff bundle to the LLM (structured output)
and merges the result into the flagged finding. Tests inject a fake chat model
so they stay fully offline: the fake exposes ``with_structured_output(schema)``
returning an object whose ``invoke`` yields a canned :class:`AnalyzerOutput`.
"""

from __future__ import annotations

import pytest

from tracker.agents import analyzer
from tracker.assembler import flagged_preflight_finding
from tracker.models import AnalyzerOutput, Conflict, Cve, EvidenceBundle, RepoConfig, TrialMerge


class _Structured:
    def __init__(self, out: AnalyzerOutput):
        self._out = out
        self.seen = None

    def invoke(self, messages):
        self.seen = messages
        return self._out


class _FakeModel:
    """Stand-in for the LangChain chat model with structured output."""

    def __init__(self, out: AnalyzerOutput):
        self.structured = _Structured(out)

    def with_structured_output(self, _schema):
        return self.structured


class _BoomModel:
    def with_structured_output(self, _schema):
        raise RuntimeError("api down")


def _repo() -> RepoConfig:
    return RepoConfig(
        name="coredns/coredns",
        upstream="https://github.com/coredns/coredns",
        canonical_repo="https://github.com/canonical/mx-coredns",
        canonical_branch="canonical/1.14-26.04/stable",
        current_upstream_tag="v1.14.6",
    )


def _flagged():
    return flagged_preflight_finding(
        "v1.14.7",
        risk="medium",
        summary="Trial merge conflicts in 1 file(s).",
        trial_merge=TrialMerge(result="conflict", conflicting_paths=["a.go"]),
        cve_refs_found=True,
    )


def test_system_prompt_loaded_at_runtime():
    prompt = analyzer.system_prompt()
    assert "mixed sources" in prompt.lower()
    assert "superdistro" in prompt.lower()


def test_merges_analysis_into_finding():
    out = AnalyzerOutput(
        risk="high",
        cves=[Cve(cve="CVE-2026-1", severity="high", affects="apiserver", detail="d")],
        conflicts=[Conflict(path="a.go", cause="local patch overlap", resolution_hint="reapply")],
        notes_for_reviewer="check our patches",
    )
    finding = analyzer.analyze_tag(
        _repo(),
        "v1.14.7",
        _flagged(),
        EvidenceBundle(cve_refs=["CVE-2026-1"], conflict_hunks="<<<<<<<"),
        model=_FakeModel(out),
    )
    assert finding.analysis is not None
    assert finding.analysis.cves[0].cve == "CVE-2026-1"
    assert finding.analysis.conflicts[0].path == "a.go"
    assert finding.risk == "high"  # escalated from medium
    assert finding.notes_for_reviewer == "check our patches"


def test_never_downgrades_risk():
    out = AnalyzerOutput(risk="low")
    finding = analyzer.analyze_tag(
        _repo(), "v1.14.7", _flagged(), EvidenceBundle(), model=_FakeModel(out)
    )
    assert finding.risk == "medium"  # preflight risk preserved


def test_highlights_and_dependencies_flow_through():
    from tracker.models import Dependency, Highlight

    out = AnalyzerOutput(
        risk="medium",
        highlights=[
            Highlight(kind="behaviour_change", area="kubelet", detail="eviction default changed")
        ],
        dependencies=[
            Dependency(name="golang.org/x/net", **{"from": "v1", "to": "v2"}, reason="cve_fix")
        ],
    )
    finding = analyzer.analyze_tag(
        _repo(), "v1.14.7", _flagged(), EvidenceBundle(), model=_FakeModel(out)
    )
    assert finding.highlights[0].kind == "behaviour_change"
    assert finding.dependencies[0].reason == "cve_fix"


def test_evidence_reaches_the_model():
    fake = _FakeModel(AnalyzerOutput())
    analyzer.analyze_tag(
        _repo(),
        "v1.14.7",
        _flagged(),
        EvidenceBundle(cve_refs=["CVE-2026-9"], commit_messages=["touch pkg/x.go"]),
        model=fake,
    )
    blob = str(fake.structured.seen)
    assert "CVE-2026-9" in blob
    assert "pkg/x.go" in blob
    assert "v1.14.7" in blob


def test_graceful_degradation_on_model_error():
    finding = analyzer.analyze_tag(
        _repo(), "v1.14.7", _flagged(), EvidenceBundle(), model=_BoomModel()
    )
    # Still reported as flagged; analysis stays absent; a note explains the gap.
    assert finding.preflight.decision == "flagged"
    assert finding.analysis is None
    assert "analysis unavailable" in (finding.notes_for_reviewer or "").lower()


def test_analyzer_node_enriches_state(monkeypatch):
    out = AnalyzerOutput(risk="medium", notes_for_reviewer="node note")
    monkeypatch.setattr(analyzer, "get_chat_model", lambda: _FakeModel(out))
    state = {
        "repo": _repo(),
        "tag": "v1.14.7",
        "tag_finding": _flagged(),
        "evidence": EvidenceBundle(),
    }
    result = analyzer.analyzer_node(state)
    assert result["tag_finding"].notes_for_reviewer == "node note"


@pytest.mark.parametrize("configured", [None])
def test_analyzer_node_uses_default_model(monkeypatch, configured):
    # analyzer_node has no injection seam; it must resolve the model itself.
    called = {}
    monkeypatch.setattr(
        analyzer, "get_chat_model", lambda: called.setdefault("m", _FakeModel(AnalyzerOutput()))
    )
    analyzer.analyzer_node(
        {"repo": _repo(), "tag": "v1", "tag_finding": _flagged(), "evidence": EvidenceBundle()}
    )
    assert "m" in called


def test_flagged_tag_flows_through_subgraph_to_analysis(monkeypatch):
    # End-to-end: preflight flags a conflict -> analyzer enriches -> the
    # assembled repo finding carries the analysis block. Both network seams
    # (preflight evidence gathering, the chat model) are faked.
    from tracker.agents import preflight
    from tracker.graph import run_repo_subgraph
    from tracker.models import RepoJob

    monkeypatch.setattr(
        preflight,
        "gather_evidence",
        lambda repo, tag, **kw: (
            EvidenceBundle(conflict_hunks="<<<<<<< HEAD", commit_messages=["a"]),
            TrialMerge(result="conflict", conflicting_paths=["a.go"]),
        ),
    )
    out = AnalyzerOutput(
        risk="high",
        cves=[Cve(cve="CVE-2026-2", severity="high", affects="resolver", detail="d")],
        conflicts=[Conflict(path="a.go", cause="patch overlap", resolution_hint="reapply")],
        notes_for_reviewer="verify our resolver patch",
    )
    monkeypatch.setattr(analyzer, "get_chat_model", lambda: _FakeModel(out))

    job = RepoJob(repo=_repo(), new_tags=["v1.14.7"])
    finding = run_repo_subgraph(job)

    tag_finding = finding.new_tags[0]
    assert tag_finding.preflight.decision == "flagged"
    assert tag_finding.risk == "high"
    assert tag_finding.analysis is not None
    assert tag_finding.analysis.cves[0].cve == "CVE-2026-2"
    assert tag_finding.analysis.conflicts[0].path == "a.go"
    assert tag_finding.notes_for_reviewer == "verify our resolver patch"


def test_clean_tag_skips_analyzer(monkeypatch):
    # A clean, CVE-free tag must never reach the analyzer (cost gate).
    from tracker.agents import preflight
    from tracker.graph import run_repo_subgraph
    from tracker.models import RepoJob

    monkeypatch.setattr(
        preflight,
        "gather_evidence",
        lambda repo, tag, **kw: (EvidenceBundle(), TrialMerge(result="clean")),
    )

    def _boom():
        raise AssertionError("analyzer must not run on a clean tag")

    monkeypatch.setattr(analyzer, "get_chat_model", _boom)

    finding = run_repo_subgraph(RepoJob(repo=_repo(), new_tags=["v1.14.7"]))
    assert finding.new_tags[0].preflight.decision == "clean"
    assert finding.new_tags[0].analysis is None
