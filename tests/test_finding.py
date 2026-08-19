"""Finding schema + assembler tests (Milestone 3)."""

from __future__ import annotations

from tracker.assembler import (
    assemble_finding,
    clean_tag_finding,
    flagged_preflight_finding,
    pending_tag_finding,
)
from tracker.models import (
    Analysis,
    Conflict,
    Cve,
    Dependency,
    Finding,
    Highlight,
    NewTagFinding,
    Preflight,
    RepoConfig,
    TrialMerge,
)


def _repo(tag: str = "v1.36.5") -> RepoConfig:
    return RepoConfig(
        name="kubernetes/kubernetes",
        upstream="https://github.com/kubernetes/kubernetes",
        canonical_repo="https://github.com/canonical/mx-kubernetes",
        canonical_branch="canonical/1.36-26.04/stable",
        current_upstream_tag=tag,
    )


def _flagged_tag() -> NewTagFinding:
    return NewTagFinding(
        tag="v1.36.6",
        risk="medium",
        summary="Mostly bugfixes; one kubelet eviction behaviour change.",
        preflight=Preflight(
            docs_reviewed=True,
            cve_refs_found=True,
            trial_merge=TrialMerge(
                result="conflict",
                conflicting_paths=["pkg/kubelet/eviction/eviction_manager.go"],
                output_ref="artifact://merge-log/123",
            ),
            decision="flagged",
        ),
        highlights=[
            Highlight(
                kind="behaviour_change",
                area="kubelet",
                detail="Eviction threshold default changed.",
                upstream_ref="https://github.com/kubernetes/kubernetes/pull/1",
            ),
            Highlight(
                kind="cve_fix",
                component="kube-apiserver",
                cve="CVE-2026-1111",
                severity="high",
                detail="Auth bypass fixed.",
            ),
        ],
        dependencies=[
            Dependency(
                name="golang.org/x/net", from_="vA", to="vB", reason="cve_fix", cve="CVE-2026-2222"
            ),
        ],
        analysis=Analysis(
            cves=[
                Cve(
                    cve="CVE-2026-1111",
                    severity="high",
                    affects="apiserver auth",
                    detail="Bypass under condition Z.",
                )
            ],
            conflicts=[
                Conflict(
                    path="pkg/kubelet/eviction/eviction_manager.go",
                    cause="Local patch overlaps upstream default.",
                    resolution_hint="Re-apply threshold override.",
                )
            ],
        ),
        notes_for_reviewer="Confirm kubelet eviction patches still apply.",
    )


# --- computed fields -----------------------------------------------------


def test_derived_fields_from_repo():
    finding = Finding(repo=_repo("v1.36.5"), new_tags=[])
    assert finding.tracked_version == "1.36.x"
    assert finding.last_merged_tag == "v1.36.5"
    # Computed fields appear in the serialized output the reporter emits.
    dumped = finding.model_dump()
    assert dumped["tracked_version"] == "1.36.x"
    assert dumped["last_merged_tag"] == "v1.36.5"


# --- clean shape ---------------------------------------------------------


def test_clean_tag_finding_is_short():
    entry = clean_tag_finding("v1.14.7", summary="Routine bugfixes.")
    assert entry.preflight.decision == "clean"
    assert entry.preflight.trial_merge.result == "clean"
    assert entry.analysis is None
    assert entry.risk == "low"


def test_clean_finding_roundtrips():
    finding = assemble_finding(_repo("v1.14.6"), [clean_tag_finding("v1.14.7", summary="ok")])
    restored = Finding.model_validate(finding.model_dump())
    assert restored == finding
    assert restored.new_tags[0].analysis is None


# --- flagged shape -------------------------------------------------------


def test_flagged_finding_roundtrips():
    finding = assemble_finding(_repo("v1.36.5"), [_flagged_tag()])
    restored = Finding.model_validate(finding.model_dump())
    assert restored == finding

    tag = restored.new_tags[0]
    assert tag.preflight.decision == "flagged"
    assert tag.analysis is not None
    assert tag.analysis.cves[0].cve == "CVE-2026-1111"
    assert tag.analysis.conflicts[0].path.endswith("eviction_manager.go")


def test_dependency_from_alias_serialization():
    finding = assemble_finding(_repo("v1.36.5"), [_flagged_tag()])
    dep = finding.model_dump(by_alias=True)["new_tags"][0]["dependencies"][0]
    # Spec uses "from"/"to" keys, not the Python-safe "from_".
    assert dep["from"] == "vA"
    assert dep["to"] == "vB"
    assert "from_" not in dep
    # And it round-trips back from the aliased shape.
    Finding.model_validate(finding.model_dump(by_alias=True))


# --- assembler -----------------------------------------------------------


def test_assemble_sorts_tags_oldest_to_newest():
    entries = [
        clean_tag_finding("v1.14.9", summary="c"),
        clean_tag_finding("v1.14.7", summary="a"),
        clean_tag_finding("v1.14.8", summary="b"),
    ]
    finding = assemble_finding(_repo("v1.14.6"), entries)
    assert [nt.tag for nt in finding.new_tags] == ["v1.14.7", "v1.14.8", "v1.14.9"]


def test_pending_tag_finding_is_valid_and_labelled():
    entry = pending_tag_finding("v1.14.7")
    assert entry.tag == "v1.14.7"
    assert "Pending" in entry.summary
    assert entry.preflight.decision == "clean"


def test_flagged_preflight_finding_shape():
    entry = flagged_preflight_finding(
        "v1.14.7",
        risk="medium",
        summary="Trial merge conflicts in 1 file(s).",
        trial_merge=TrialMerge(result="conflict", conflicting_paths=["a.go"]),
    )
    assert entry.risk == "medium"
    assert entry.preflight.decision == "flagged"
    assert entry.preflight.cve_refs_found is False
    assert entry.preflight.trial_merge.result == "conflict"
    # Deep analysis is the analyzer's job (M5); preflight never fills it.
    assert entry.analysis is None


def test_flagged_preflight_finding_carries_cve_flag():
    entry = flagged_preflight_finding(
        "v1.14.7",
        risk="medium",
        summary="Clean merge but CVE referenced.",
        trial_merge=TrialMerge(result="clean"),
        cve_refs_found=True,
    )
    assert entry.preflight.cve_refs_found is True
    assert entry.preflight.decision == "flagged"


def test_evidence_bundle_defaults_empty():
    from tracker.models import EvidenceBundle

    bundle = EvidenceBundle()
    assert bundle.release_notes == ""
    assert bundle.commit_messages == []
    assert bundle.changed_files == []
    assert bundle.cve_refs == []
    assert bundle.trial_merge_output == ""
    assert bundle.conflict_hunks == ""


def test_analyzer_output_composes_finding_parts():
    from tracker.models import AnalyzerOutput

    out = AnalyzerOutput(
        risk="high",
        cves=[Cve(cve="CVE-2026-1", severity="high", affects="apiserver", detail="d")],
        conflicts=[Conflict(path="a.go", cause="c", resolution_hint="h")],
        notes_for_reviewer="check patches",
    )
    assert out.risk == "high"
    assert out.highlights == []
    assert out.dependencies == []
    assert out.cves[0].cve == "CVE-2026-1"
    assert out.conflicts[0].path == "a.go"
    assert out.notes_for_reviewer == "check patches"


def test_analyzer_output_defaults():
    from tracker.models import AnalyzerOutput

    out = AnalyzerOutput()
    assert out.risk == "medium"
    assert out.cves == []
    assert out.conflicts == []
    assert out.notes_for_reviewer is None
