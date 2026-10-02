"""Mattermost notification tests (Milestone 7 delivery).

The notifier aggregates this run's per-release-set reports (the
``report-<set>.json`` artifacts written by ``tracker.main``), builds one brief
markdown message listing the components with new tags, and POSTs it to a
Mattermost
incoming webhook. Tests are fully offline: message building is pure, and the
HTTP POST is monkeypatched. They pin: the component list (From -> To per
component grouped by set), the risk tally, the report link, the quiet-run
"stay silent" contract, and the CLI's no-op-when-unset behaviour.
"""

from __future__ import annotations

from pathlib import Path

from tracker import notify
from tracker.assembler import clean_tag_finding, flagged_preflight_finding
from tracker.models import Finding, RepoConfig, TrialMerge
from tracker.reports import SetReport, save_report

# Internal-repo Pages URLs carry a random host, not <org>.github.io/<repo>.
PREVIEW_URL = "https://fluffy-adventure-4m5qz8.pages.github.io/pr-preview/pr-42/"
PR_URL = "https://github.com/canonical/mx-upstream-tracker/pull/42"


def _repo(name: str, tag: str) -> RepoConfig:
    return RepoConfig(
        name=name,
        upstream=f"https://github.com/{name}",
        canonical_repo=f"https://github.com/canonical/mx-{name.replace('/', '-')}",
        canonical_branch="canonical/x/stable",
        current_upstream_tag=tag,
    )


def _high() -> Finding:
    tag = flagged_preflight_finding(
        "v1.37.1",
        risk="high",
        summary="kubelet eviction default changed; CVE fix.",
        trial_merge=TrialMerge(result="conflict", conflicting_paths=["pkg/kubelet/x.go"]),
        cve_refs_found=True,
    )
    return Finding(repo=_repo("kubernetes/kubernetes", "v1.37.0"), new_tags=[tag])


def _clean() -> Finding:
    return Finding(
        repo=_repo("coredns/coredns", "v1.14.6"),
        new_tags=[clean_tag_finding("v1.14.7", summary="Trial merge clean; 3 commit(s).")],
    )


def _medium() -> Finding:
    tag = flagged_preflight_finding(
        "v2.3.1",
        risk="medium",
        summary="Routine bugfixes; one dependency bump.",
        trial_merge=TrialMerge(result="clean"),
        cve_refs_found=True,
    )
    return Finding(repo=_repo("containerd/containerd", "v2.3.0"), new_tags=[tag])


# --- build_message -------------------------------------------------------


def test_build_message_lists_components_grouped_by_set_with_link():
    reports = [
        SetReport("1.36", "2026-08-27T09:00:00+00:00", [_clean(), _medium()]),
        SetReport("1.37", "2026-08-27T09:00:00+00:00", [_high()]),
    ]

    msg = notify.build_message(reports, PREVIEW_URL, PR_URL)

    assert msg is not None
    # Component count across all sets (3 findings = 3 components with new tags).
    assert "3" in msg
    # Grouped by set headers.
    assert "Kubernetes 1.36" in msg
    assert "Kubernetes 1.37" in msg
    # Per-component From -> To lines.
    assert "kubernetes/kubernetes" in msg
    assert "v1.37.0" in msg and "v1.37.1" in msg
    assert "coredns/coredns" in msg
    assert "v1.14.6" in msg and "v1.14.7" in msg
    # Risk tally reflects 1 high, 1 medium, 1 low.
    assert "1 high" in msg
    assert "1 medium" in msg
    assert "1 low" in msg
    # Preview dashboard + PR links present.
    assert f"[Dashboard preview]({PREVIEW_URL})" in msg
    assert f"[Pull request]({PR_URL})" in msg


def test_build_message_quiet_run_returns_none():
    reports = [
        SetReport("1.36", None, []),
        SetReport("1.37", None, []),
    ]
    assert notify.build_message(reports, PREVIEW_URL, PR_URL) is None


def test_build_message_without_urls_omits_links():
    reports = [SetReport("1.37", None, [_high()])]
    msg = notify.build_message(reports, None, None)
    assert msg is not None
    assert "Dashboard preview" not in msg
    assert "Pull request" not in msg


def test_build_message_skips_empty_sets_in_body():
    reports = [
        SetReport("1.36", None, []),  # quiet set: no header
        SetReport("1.37", None, [_high()]),
    ]
    msg = notify.build_message(reports, PREVIEW_URL, PR_URL)
    assert msg is not None
    assert "Kubernetes 1.36" not in msg
    assert "Kubernetes 1.37" in msg


# --- post_message --------------------------------------------------------


def test_post_message_posts_text_payload(monkeypatch):
    captured: dict = {}

    def fake_post(url, json=None, timeout=None):  # noqa: A002 - mirror httpx.post
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(notify.httpx, "post", fake_post)

    notify.post_message("https://mm.example.com/hooks/abc", "hello")

    assert captured["url"] == "https://mm.example.com/hooks/abc"
    assert captured["json"] == {"text": "hello"}
    assert captured["timeout"] is not None


def test_post_message_raises_on_http_error(monkeypatch):
    def fake_post(url, json=None, timeout=None):  # noqa: A002
        return _FakeResponse(raise_error=RuntimeError("boom"))

    monkeypatch.setattr(notify.httpx, "post", fake_post)

    raised = False
    try:
        notify.post_message("https://mm.example.com/hooks/abc", "hello")
    except RuntimeError:
        raised = True
    assert raised


# --- CLI -----------------------------------------------------------------


def _write_set(root: Path, release_set: str, findings: list[Finding]) -> None:
    save_report(
        root / f"report-{release_set.replace('.', '-')}.json",
        SetReport(release_set, "2026-08-27T09:00:00+00:00", findings),
    )


def test_cli_noop_when_webhook_unset(tmp_path: Path, monkeypatch):
    _write_set(tmp_path, "1.37", [_high()])
    monkeypatch.delenv("MATTERMOST_WEBHOOK_URL", raising=False)

    posted: list = []
    monkeypatch.setattr(notify, "post_message", lambda url, text: posted.append((url, text)))

    notify.main([str(tmp_path), "--preview-url", PREVIEW_URL, "--pr-url", PR_URL])

    assert posted == []  # nothing sent without a webhook


def test_cli_posts_when_new_tags(tmp_path: Path, monkeypatch):
    _write_set(tmp_path, "1.37", [_high()])
    monkeypatch.setenv("MATTERMOST_WEBHOOK_URL", "https://mm.example.com/hooks/abc")

    posted: list = []
    monkeypatch.setattr(notify, "post_message", lambda url, text: posted.append((url, text)))

    notify.main([str(tmp_path), "--preview-url", PREVIEW_URL, "--pr-url", PR_URL])

    assert len(posted) == 1
    url, text = posted[0]
    assert url == "https://mm.example.com/hooks/abc"
    assert "kubernetes/kubernetes" in text
    assert PREVIEW_URL in text and PR_URL in text


def test_cli_quiet_run_does_not_post(tmp_path: Path, monkeypatch):
    _write_set(tmp_path, "1.37", [])  # quiet set
    monkeypatch.setenv("MATTERMOST_WEBHOOK_URL", "https://mm.example.com/hooks/abc")

    posted: list = []
    monkeypatch.setattr(notify, "post_message", lambda url, text: posted.append((url, text)))

    notify.main([str(tmp_path), "--preview-url", PREVIEW_URL, "--pr-url", PR_URL])

    assert posted == []


class _FakeResponse:
    def __init__(self, raise_error: Exception | None = None) -> None:
        self._raise_error = raise_error

    def raise_for_status(self) -> None:
        if self._raise_error is not None:
            raise self._raise_error
