"""Preflight gate tests (Milestone 4).

Deterministic, merge-driven decision: a clean trial merge is a short clean
finding; a conflict or a hard git error is flagged (with risk graded by which).
All evidence sources (release notes, tag compare, trial merge) are faked, so the
tests are fully offline.
"""

from __future__ import annotations

from tracker.agents import preflight
from tracker.models import RepoConfig


def _repo() -> RepoConfig:
    return RepoConfig(
        name="coredns/coredns",
        upstream="https://github.com/coredns/coredns",
        canonical_repo="https://github.com/canonical/mx-coredns",
        canonical_branch="canonical/1.14-26.04/stable",
        current_upstream_tag="v1.14.6",
    )


def _stub_evidence(monkeypatch, *, merge, notes="## notes", compare=None):
    compare = compare or {"total_commits": 3, "commit_messages": ["fix"], "files": ["a.go"]}
    monkeypatch.setattr(preflight, "get_release_notes", lambda repo, tag: notes)
    monkeypatch.setattr(preflight, "compare_tags", lambda repo, base, head: compare)
    monkeypatch.setattr(preflight, "trial_merge_fork", lambda repo, tag, **kw: merge)


def test_clean_merge_is_short_clean_finding(monkeypatch):
    _stub_evidence(
        monkeypatch, merge={"result": "clean", "conflicting_paths": [], "output_ref": None}
    )

    finding = preflight.assess_tag(_repo(), "v1.14.7")
    assert finding.tag == "v1.14.7"
    assert finding.preflight.decision == "clean"
    assert finding.risk == "low"
    assert finding.preflight.trial_merge.result == "clean"
    assert finding.analysis is None


def test_conflict_merge_is_flagged_medium(monkeypatch):
    _stub_evidence(
        monkeypatch,
        merge={"result": "conflict", "conflicting_paths": ["a.go", "b.go"], "output_ref": "log"},
    )

    finding = preflight.assess_tag(_repo(), "v1.14.7")
    assert finding.preflight.decision == "flagged"
    assert finding.risk == "medium"
    assert finding.preflight.trial_merge.conflicting_paths == ["a.go", "b.go"]
    assert finding.preflight.trial_merge.output_ref == "log"


def test_error_merge_is_flagged_high(monkeypatch):
    _stub_evidence(
        monkeypatch,
        merge={"result": "error", "conflicting_paths": [], "output_ref": None},
    )

    finding = preflight.assess_tag(_repo(), "v9.9.9")
    assert finding.preflight.decision == "flagged"
    assert finding.risk == "high"


def test_docs_reviewed_reflects_release_notes(monkeypatch):
    _stub_evidence(
        monkeypatch,
        merge={"result": "clean", "conflicting_paths": [], "output_ref": None},
        notes="",  # no GitHub release attached to this tag
    )
    finding = preflight.assess_tag(_repo(), "v1.14.7")
    assert finding.preflight.docs_reviewed is False


def test_cves_are_out_of_scope_in_m4(monkeypatch):
    _stub_evidence(
        monkeypatch, merge={"result": "clean", "conflicting_paths": [], "output_ref": None}
    )
    finding = preflight.assess_tag(_repo(), "v1.14.7")
    assert finding.preflight.cve_refs_found is False


# --- graph node + routing ------------------------------------------------


def test_preflight_node_sets_tag_finding(monkeypatch):
    _stub_evidence(
        monkeypatch, merge={"result": "clean", "conflicting_paths": [], "output_ref": None}
    )
    out = preflight.preflight_node({"repo": _repo(), "tag": "v1.14.7"})
    assert out["tag_finding"].tag == "v1.14.7"


def test_route_flagged_goes_to_analyzer(monkeypatch):
    _stub_evidence(
        monkeypatch,
        merge={"result": "conflict", "conflicting_paths": ["a.go"], "output_ref": None},
    )
    state = {"repo": _repo(), "tag": "v1.14.7"}
    state.update(preflight.preflight_node(state))
    assert preflight.route_after_preflight(state) == "analyzer"


def test_route_clean_goes_to_assemble(monkeypatch):
    _stub_evidence(
        monkeypatch, merge={"result": "clean", "conflicting_paths": [], "output_ref": None}
    )
    state = {"repo": _repo(), "tag": "v1.14.7"}
    state.update(preflight.preflight_node(state))
    assert preflight.route_after_preflight(state) == "assemble"
