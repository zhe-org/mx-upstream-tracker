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
        canonical_branch="canonical/1.14/stable",
        current_upstream_tag="v1.14.6",
    )


def _stub_evidence(monkeypatch, *, merge, notes="## notes", compare=None):
    compare = compare or {"commit_messages": ["fix"]}
    merge = {"output": "", "conflict_hunks": "", **merge}
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


def test_no_cve_ref_stays_clean(monkeypatch):
    # Default notes carry no CVE id, so a clean merge stays clean.
    _stub_evidence(
        monkeypatch, merge={"result": "clean", "conflicting_paths": [], "output_ref": None}
    )
    finding = preflight.assess_tag(_repo(), "v1.14.7")
    assert finding.preflight.cve_refs_found is False
    assert finding.preflight.decision == "clean"


def test_cve_ref_flags_clean_merge(monkeypatch):
    # A clean trial merge that nevertheless references a CVE must be flagged so
    # the analyzer runs (spec: "flagged — anything of note").
    _stub_evidence(
        monkeypatch,
        merge={"result": "clean", "conflicting_paths": [], "output_ref": None},
        notes="Security: fixes CVE-2026-1234 in the resolver.",
    )
    finding = preflight.assess_tag(_repo(), "v1.14.7")
    assert finding.preflight.decision == "flagged"
    assert finding.preflight.cve_refs_found is True
    assert finding.risk == "medium"


def test_cve_ref_in_commit_messages_flags(monkeypatch):
    _stub_evidence(
        monkeypatch,
        merge={"result": "clean", "conflicting_paths": [], "output_ref": None},
        notes="",
        compare={"commit_messages": ["bump for CVE-2026-9999"]},
    )
    finding = preflight.assess_tag(_repo(), "v1.14.7")
    assert finding.preflight.decision == "flagged"
    assert finding.preflight.cve_refs_found is True


def test_node_emits_evidence_bundle(monkeypatch):
    _stub_evidence(
        monkeypatch,
        merge={
            "result": "conflict",
            "conflicting_paths": ["a.go"],
            "output_ref": None,
            "output": "merge log",
            "conflict_hunks": "<<<<<<< HEAD",
        },
        notes="Fixes CVE-2026-1111",
        compare={"commit_messages": ["a", "b"]},
    )
    out = preflight.preflight_node({"repo": _repo(), "tag": "v1.14.7"})
    bundle = out["evidence"]
    assert bundle.conflict_hunks == "<<<<<<< HEAD"
    assert bundle.trial_merge_output == "merge log"
    assert bundle.cve_refs == ["CVE-2026-1111"]
    assert bundle.commit_messages == ["a", "b"]
    assert out["tag_finding"].preflight.decision == "flagged"


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
