"""Trial-merge git operations tests (Milestone 4).

The trial merge shells out to ``git`` in a throwaway workspace. Tests fake
``subprocess.run`` so no real cloning/merging happens: they pin the classifier
(clean / conflict / error), the conflicting-path parsing, the no-raise-on-
conflict contract, and temp-workspace cleanup.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tracker.models import RepoConfig
from tracker.tools import git_ops


def _repo() -> RepoConfig:
    return RepoConfig(
        name="coredns/coredns",
        upstream="https://github.com/coredns/coredns",
        canonical_repo="https://github.com/canonical/mx-coredns",
        canonical_branch="canonical/1.14-26.04/stable",
        current_upstream_tag="v1.14.6",
    )


class _FakeGit:
    """Scriptable subprocess.run stand-in keyed by the git subcommand."""

    def __init__(self, results: dict[str, subprocess.CompletedProcess]):
        self.results = results
        self.calls: list[list[str]] = []

    def __call__(self, cmd, *args, **kwargs):
        self.calls.append(cmd)
        # cmd is like ["git", "-C", dest, "<subcommand>", ...]
        subcommand = cmd[3] if cmd[:3] == ["git", "-C", cmd[2]] else cmd[1]
        result = self.results.get(subcommand, subprocess.CompletedProcess(cmd, 0, "", ""))
        return result


def _ok(cmd="git", out="", err="") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(cmd, 0, out, err)


def _fail(code=1, out="", err="") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess("git", code, out, err)


def test_trial_merge_clean(monkeypatch, tmp_path: Path):
    fake = _FakeGit({"merge": _ok(out="Merge made by the 'ort' strategy.")})
    monkeypatch.setattr(git_ops.subprocess, "run", fake)

    result = git_ops.trial_merge(str(tmp_path), "https://u", "v1.14.7")
    assert result["result"] == "clean"
    assert result["conflicting_paths"] == []


def test_trial_merge_conflict_lists_paths(monkeypatch, tmp_path: Path):
    fake = _FakeGit(
        {
            "merge": _fail(
                out="CONFLICT (content): Merge conflict in plugin/kubernetes/handler.go"
            ),
            "diff": _ok(out="plugin/kubernetes/handler.go\nplugin/forward/forward.go\n"),
        }
    )
    monkeypatch.setattr(git_ops.subprocess, "run", fake)

    result = git_ops.trial_merge(str(tmp_path), "https://u", "v1.14.7")
    assert result["result"] == "conflict"
    assert result["conflicting_paths"] == [
        "plugin/kubernetes/handler.go",
        "plugin/forward/forward.go",
    ]


def test_trial_merge_captures_conflict_hunks(monkeypatch, tmp_path: Path):
    # Two distinct `git diff` invocations: --name-only (paths) vs plain (hunks).
    # Use an arg-aware _git fake so each returns something different.
    calls: list[tuple[str, ...]] = []

    def fake_git(dest, *args):
        calls.append(args)
        if args[:1] == ("merge",) and "--abort" not in args:
            return _fail(out="CONFLICT (content): Merge conflict in a.go")
        if args[:2] == ("diff", "--name-only"):
            return _ok(out="a.go\n")
        if args[:1] == ("diff",):
            return _ok(out="<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> v1\n")
        return _ok()

    monkeypatch.setattr(git_ops, "_git", fake_git)

    result = git_ops.trial_merge(str(tmp_path), "https://u", "v1")
    assert result["result"] == "conflict"
    assert result["conflicting_paths"] == ["a.go"]
    assert "<<<<<<<" in result["conflict_hunks"]
    # The in-progress merge is still aborted so the workspace is left inert.
    assert ("merge", "--abort") in calls


def test_trial_merge_no_hunks_when_clean(monkeypatch, tmp_path: Path):
    fake = _FakeGit({"merge": _ok(out="Merge made by the 'ort' strategy.")})
    monkeypatch.setattr(git_ops.subprocess, "run", fake)

    result = git_ops.trial_merge(str(tmp_path), "https://u", "v1.14.7")
    assert result["conflict_hunks"] == ""


def test_trial_merge_error_when_fetch_fails(monkeypatch, tmp_path: Path):
    fake = _FakeGit({"fetch": _fail(err="fatal: couldn't find remote ref v9.9.9")})
    monkeypatch.setattr(git_ops.subprocess, "run", fake)

    result = git_ops.trial_merge(str(tmp_path), "https://u", "v9.9.9")
    assert result["result"] == "error"
    assert result["conflicting_paths"] == []


def test_trial_merge_never_raises_on_conflict(monkeypatch, tmp_path: Path):
    fake = _FakeGit({"merge": _fail(out="CONFLICT (content): Merge conflict in x")})
    monkeypatch.setattr(git_ops.subprocess, "run", fake)
    # Must return a dict, not raise, even though git exited non-zero.
    assert git_ops.trial_merge(str(tmp_path), "https://u", "v1.14.7")["result"] == "conflict"


def test_trial_merge_fork_cleans_up_workspace(monkeypatch, tmp_path: Path):
    created: list[str] = []

    def fake_mkdtemp(*a, **k):
        d = tmp_path / "workspace"
        d.mkdir()
        created.append(str(d))
        return str(d)

    monkeypatch.setattr(git_ops.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(git_ops.subprocess, "run", _FakeGit({"merge": _ok()}))

    result = git_ops.trial_merge_fork(_repo(), "v1.14.7", token="t")
    assert result["result"] == "clean"
    # The throwaway workspace must be gone afterwards.
    assert created and not Path(created[0]).exists()


def test_trial_merge_fork_propagates_output_and_hunks(monkeypatch, tmp_path: Path):
    def fake_mkdtemp(*a, **k):
        d = tmp_path / "ws"
        d.mkdir()
        return str(d)

    monkeypatch.setattr(git_ops.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(git_ops, "clone_fork", lambda *a, **k: None)
    monkeypatch.setattr(
        git_ops,
        "trial_merge",
        lambda *a, **k: {
            "result": "conflict",
            "conflicting_paths": ["a.go"],
            "output": "merge log",
            "conflict_hunks": "<<<<<<<",
        },
    )

    result = git_ops.trial_merge_fork(_repo(), "v1.14.7", token="t")
    assert result["output"] == "merge log"
    assert result["conflict_hunks"] == "<<<<<<<"


def test_trial_merge_fork_error_when_clone_fails(monkeypatch, tmp_path: Path):
    # A clone failure (e.g. auth/network) must degrade to an 'error' finding,
    # never crash the run. The workspace is still cleaned up.
    created: list[str] = []

    def fake_mkdtemp(*a, **k):
        d = tmp_path / "ws"
        d.mkdir()
        created.append(str(d))
        return str(d)

    monkeypatch.setattr(git_ops.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(
        git_ops.subprocess, "run", _FakeGit({"clone": _fail(err="fatal: authentication failed")})
    )

    result = git_ops.trial_merge_fork(_repo(), "v1.14.7", token="t")
    assert result["result"] == "error"
    assert not Path(created[0]).exists()


def test_authed_url_embeds_token_for_github():
    assert (
        git_ops._authed_url("https://github.com/canonical/mx-x", "TKN")
        == "https://x-access-token:TKN@github.com/canonical/mx-x"
    )


def test_authed_url_unchanged_without_token():
    assert git_ops._authed_url("https://github.com/x", None) == "https://github.com/x"


def test_clone_fork_error_never_leaks_token(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(git_ops.subprocess, "run", _FakeGit({"clone": _fail(err="fatal: nope")}))
    with pytest.raises(RuntimeError) as exc:
        git_ops.clone_fork("https://github.com/canonical/mx-x", str(tmp_path), "b", token="SECRET")
    assert "SECRET" not in str(exc.value)
