"""GitHub release-notes / compare wrapper tests (Milestone 4).

Thin PyGithub wrappers, so we fake the client: assert we return the release
body, degrade to empty string when there is no release, and shape the
tag-to-tag comparison into the small dict the preflight gate consumes.
"""

from __future__ import annotations

from github import GithubException

from tracker.tools import github as gh


class _FakeRelease:
    def __init__(self, body: str):
        self.body = body


class _FakeCommit:
    def __init__(self, message: str):
        self.commit = type("C", (), {"message": message})()


class _FakeFile:
    def __init__(self, filename: str):
        self.filename = filename


class _FakeComparison:
    total_commits = 2
    commits = [_FakeCommit("fix: a"), _FakeCommit("fix: b")]
    files = [_FakeFile("plugin/a.go"), _FakeFile("plugin/b.go")]


class _FakeRepo:
    def __init__(self, *, release=None, raise_on_release=False):
        self._release = release
        self._raise = raise_on_release

    def get_release(self, tag):
        if self._raise:
            raise GithubException(404, {"message": "Not Found"}, None)
        return self._release

    def compare(self, base, head):
        return _FakeComparison()


class _FakeClient:
    def __init__(self, repo):
        self._repo = repo

    def get_repo(self, name):
        return self._repo

    def close(self):
        pass


def _patch_client(monkeypatch, repo):
    monkeypatch.setattr(gh, "_client", lambda: _FakeClient(repo))


def test_get_release_notes_returns_body(monkeypatch):
    _patch_client(monkeypatch, _FakeRepo(release=_FakeRelease("## Changelog\n- fix")))
    assert gh.get_release_notes("coredns/coredns", "v1.14.7") == "## Changelog\n- fix"


def test_get_release_notes_empty_when_no_release(monkeypatch):
    _patch_client(monkeypatch, _FakeRepo(raise_on_release=True))
    assert gh.get_release_notes("coredns/coredns", "v9.9.9") == ""


def test_compare_tags_shapes_summary(monkeypatch):
    _patch_client(monkeypatch, _FakeRepo())
    out = gh.compare_tags("coredns/coredns", "v1.14.6", "v1.14.7")
    assert out["total_commits"] == 2
    assert out["files"] == ["plugin/a.go", "plugin/b.go"]
    assert out["commit_messages"] == ["fix: a", "fix: b"]
