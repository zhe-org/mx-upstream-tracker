"""Local git operations against our forks (Milestone 4).

Used to clone a fork branch into a throwaway temp workspace on the runner and
attempt a **trial merge** of a new upstream tag. The merge need not succeed: the
goal is to capture what would happen (clean apply, conflicts, or a hard git
error). Everything shells out to ``git`` via :mod:`subprocess`; nothing here ever
touches the real fork — only a local throwaway clone.

The classifier is deliberately coarse (the whole point of M4's gate):

  - ``clean``    — the merge applied with no conflicts.
  - ``conflict`` — ``git merge`` reported merge conflicts (needs a human merge).
  - ``error``    — any other git failure (bad tag, clone failure, ...), i.e. we
    could not even evaluate the merge.

``trial_merge`` never raises on a conflicting merge; it returns a structured
dict so the preflight gate can make its decision.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from tracker.config import load_settings
from tracker.models import RepoConfig

# git subcommands are given an explicit ``-C <dest>`` so we never rely on cwd.
_UPSTREAM_REMOTE = "upstream"
_GITHUB_HTTPS = "https://github.com/"


def _authed_url(url: str, token: str | None) -> str:
    """Embed ``token`` into a github.com HTTPS URL for private-fork access.

    Returns the URL unchanged when there is no token or it is not a github.com
    HTTPS URL. The token is never persisted (used only for the clone command in
    a throwaway workspace) and is scrubbed from any surfaced output.
    """
    if token and url.startswith(_GITHUB_HTTPS):
        return url.replace("https://", f"https://x-access-token:{token}@", 1)
    return url


def _scrub(text: str, token: str | None) -> str:
    return text.replace(token, "***") if token else text


def _git(dest: str, *args: str) -> subprocess.CompletedProcess:
    """Run ``git -C <dest> <args...>`` capturing output; never check the code."""
    return subprocess.run(
        ["git", "-C", dest, *args],
        capture_output=True,
        text=True,
    )


def clone_fork(fork_url: str, dest: str, branch: str, *, token: str | None = None) -> None:
    """Clone just ``branch`` of ``fork_url`` into ``dest``.

    ``--single-branch`` keeps the clone lean (one branch), but we do NOT go
    shallow: a trial merge needs the merge-base history to be meaningful. When a
    ``token`` is given it is injected for auth (our forks are private) and kept
    out of any raised error.
    """
    result = subprocess.run(
        ["git", "clone", "--single-branch", "--branch", branch, _authed_url(fork_url, token), dest],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git clone failed for {fork_url}@{branch}: {_scrub(result.stderr.strip(), token)}"
        )


def _conflicting_paths(dest: str) -> list[str]:
    """Unmerged paths after a conflicting merge (``git diff --diff-filter=U``)."""
    result = _git(dest, "diff", "--name-only", "--diff-filter=U")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def trial_merge(dest: str, upstream_url: str, tag: str) -> dict:
    """Attempt merging upstream ``tag`` into the clone at ``dest``.

    Returns ``{result, conflicting_paths, output}`` where ``result`` is one of
    ``clean | conflict | error``, plus ``conflict_hunks`` (the captured conflict
    markers on a conflicting merge, else empty). Never raises on a merge conflict.
    """

    log: list[str] = []

    def _record(step: str, proc: subprocess.CompletedProcess) -> None:
        log.append(f"$ {step}\n{proc.stdout}{proc.stderr}".rstrip())

    # Point an 'upstream' remote at the source and fetch only the target tag.
    add = _git(dest, "remote", "add", _UPSTREAM_REMOTE, upstream_url)
    _record("git remote add upstream", add)

    fetch = _git(dest, "fetch", "--no-tags", _UPSTREAM_REMOTE, "tag", tag)
    _record(f"git fetch upstream tag {tag}", fetch)
    if fetch.returncode != 0:
        return {"result": "error", "conflicting_paths": [], "output": "\n".join(log)}

    merge = _git(dest, "merge", "--no-commit", "--no-ff", tag)
    _record(f"git merge {tag}", merge)

    hunks = ""
    if merge.returncode == 0:
        result = "clean"
        conflicts: list[str] = []
    elif "CONFLICT" in (merge.stdout + merge.stderr):
        # git prints "CONFLICT (...)" lines on a conflicting merge; the unmerged
        # paths come from the diff filter. A non-zero merge WITHOUT a CONFLICT
        # marker is some other git failure (treated as error below).
        result = "conflict"
        conflicts = _conflicting_paths(dest)
        # Capture the conflict markers (<<<<<<< / >>>>>>>) for the analyzer's
        # handoff bundle, before the merge is aborted below.
        hunks = _git(dest, "diff").stdout
    else:
        result = "error"
        conflicts = []

    # Best-effort: undo the in-progress merge so the workspace is inert.
    _git(dest, "merge", "--abort")

    return {
        "result": result,
        "conflicting_paths": conflicts,
        "output": "\n".join(log),
        "conflict_hunks": hunks,
    }


def trial_merge_fork(
    repo: RepoConfig,
    tag: str,
    *,
    artifact_dir: str | Path | None = None,
    token: str | None = None,
) -> dict:
    """Clone ``repo``'s fork branch into a temp workspace and trial-merge ``tag``.

    Manages the throwaway workspace end to end (create -> clone -> merge ->
    delete). A clone/fetch failure degrades to ``result: "error"`` rather than
    raising, so one unreachable fork never aborts the whole run. When
    ``artifact_dir`` is given, the captured merge log is written there and its
    path returned as ``output_ref``.

    Returns ``{result, conflicting_paths, output_ref, output, conflict_hunks}`` —
    the trial-merge fields the preflight gate feeds into
    :class:`~tracker.models.TrialMerge` (``output``/``conflict_hunks`` flow on
    into the analyzer's evidence bundle).
    """

    if token is None:
        token = load_settings().gh_token

    workspace = tempfile.mkdtemp(prefix="tracker-merge-")
    try:
        clone_fork(repo.canonical_repo, workspace, repo.canonical_branch, token=token)
        merge = trial_merge(workspace, repo.upstream, tag)
    except Exception as exc:  # clone/fetch setup failed — we could not evaluate.
        merge = {
            "result": "error",
            "conflicting_paths": [],
            "output": f"trial merge setup failed: {_scrub(str(exc), token)}",
            "conflict_hunks": "",
        }
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    output_ref = _write_log(repo, tag, merge.get("output", ""), artifact_dir)
    return {
        "result": merge["result"],
        "conflicting_paths": merge["conflicting_paths"],
        "output_ref": output_ref,
        "output": merge.get("output", ""),
        "conflict_hunks": merge.get("conflict_hunks", ""),
    }


def _write_log(
    repo: RepoConfig,
    tag: str,
    output: str,
    artifact_dir: str | Path | None,
) -> str | None:
    if not artifact_dir:
        return None
    directory = Path(artifact_dir)
    directory.mkdir(parents=True, exist_ok=True)
    slug = f"{repo.name.replace('/', '-')}-{tag}.log"
    path = directory / slug
    path.write_text(output, encoding="utf-8")
    return str(path)
