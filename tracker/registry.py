"""Upstream-tracker registry loader + validation (Milestone 1).

The registry (``upstream-tracker.yaml``) is the version-controlled input that
drives the whole tracker: it lists, per repo, the upstream we watch, our
Canonical fork, its release branch, and the newest upstream tag our fork
currently sits on (``current_upstream_tag``). This module loads that file,
validates it against :class:`models.RepoConfig`, and fails loudly with
actionable errors so a malformed registry never silently produces an empty or
wrong run. It can also write the registry back (finalize step) with the
``current_upstream_tag`` advanced to the newest reported tag.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from tracker.models import RepoConfig


class RegistryError(ValueError):
    """Raised when the tracker registry is missing, malformed, or invalid."""


def load_registry(path: str | Path) -> list[RepoConfig]:
    """Load and validate the tracker registry at ``path``.

    Returns the list of validated :class:`RepoConfig` entries.

    Raises :class:`RegistryError` with a clear message when the file is
    missing, is not valid YAML, has the wrong top-level shape, contains no
    repos, has duplicate repo names, or any entry fails schema validation.
    """

    path = Path(path)

    if not path.exists():
        raise RegistryError(
            f"Registry file not found: {path}. Create it (see "
            "upstream-tracker.yaml) or set TRACKER_PATH."
        )

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RegistryError(f"Registry {path} is not valid YAML: {exc}") from exc

    if raw is None:
        raise RegistryError(f"Registry {path} is empty.")

    if not isinstance(raw, dict) or "repos" not in raw:
        raise RegistryError(f"Registry {path} must be a mapping with a top-level 'repos' key.")

    repos_raw = raw["repos"]
    if not isinstance(repos_raw, list) or not repos_raw:
        raise RegistryError(f"Registry {path}: 'repos' must be a non-empty list of repo entries.")

    repos: list[RepoConfig] = []
    for index, entry in enumerate(repos_raw):
        if not isinstance(entry, dict):
            raise RegistryError(
                f"Registry {path}: repos[{index}] must be a mapping, got {type(entry).__name__}."
            )
        try:
            repos.append(RepoConfig(**entry))
        except ValidationError as exc:
            label = entry.get("name", f"index {index}")
            raise RegistryError(f"Registry {path}: invalid entry for '{label}':\n{exc}") from exc

    return repos


# Field order preserved when writing the registry back out.
_FIELD_ORDER = (
    "name",
    "upstream",
    "canonical_repo",
    "canonical_branch",
    "current_upstream_tag",
)


def save_registry(path: str | Path, repos: list[RepoConfig]) -> None:
    """Write ``repos`` back to ``path`` as YAML.

    Regenerates the file from the validated models (comments are not preserved;
    the registry is machine-managed once the finalize step starts bumping
    ``current_upstream_tag``). Fields are emitted in a stable, readable order
    with a blank line between entries.
    """

    blocks: list[str] = []
    for repo in repos:
        data = repo.model_dump()
        ordered = {k: data[k] for k in _FIELD_ORDER if k in data}
        # Emit as a single-item list so PyYAML produces the '- key: value' shape.
        block = yaml.safe_dump(
            [ordered],
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )
        # Indent the block two spaces so it nests under the top-level 'repos:'.
        blocks.append("\n".join("  " + line if line else line for line in block.splitlines()))

    body = "repos:\n" + "\n\n".join(blocks) + "\n"
    Path(path).write_text(body, encoding="utf-8")
