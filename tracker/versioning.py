"""Version / tag parsing and constraint matching.

The orchestrator only cares about upstream tags that are *newer than* the
fork's baseline **on the same tracked line** implied by it. The tracked line is
the ``(prefix, major, minor)`` of the baseline; e.g.

    v1.14.6                     -> track v1.14.*   (prefix "v")
    v1.38.0-alpha.1             -> track v1.38.*
    go1.26.5                    -> track go1.26.*  (prefix "go")
    cluster-autoscaler-1.36.0   -> track cluster-autoscaler-1.36.*

A tag qualifies when it shares the same prefix and major.minor and is strictly
newer by semver precedence (:mod:`semver`), so pre-releases are included and
ordered ``alpha < beta < rc < release``. Build metadata is ignored for
ordering. Go's non-semver spellings (``go1.27rc1``, ``go1.26``) are normalized
to ``1.27.0-rc.1`` / ``1.26.0`` first. Unparseable tags are ignored.

This module is pure and deterministic (no I/O), so it is trivially testable and
kept out of the LLM path.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from semver import Version

# Leading non-digits form the prefix; the rest starts at the first digit.
_REF_RE = re.compile(r"^(?P<prefix>\D*)(?P<rest>\d.*)$")
# The version must name at least major.minor to define a tracked line.
_MAJOR_MINOR_RE = re.compile(r"^\d+\.\d+")
# Go's release spelling: 1.27rc1 / 1.27beta1 / 1.26 / 1.26.3.
_GO_RE = re.compile(r"^(?P<core>\d+\.\d+(?:\.\d+)?)(?:(?P<kind>alpha|beta|rc)(?P<num>\d+))?$")


@dataclass(frozen=True)
class ParsedRef:
    """A tag decomposed into its comparable parts."""

    prefix: str
    version: Version
    raw: str

    @property
    def line(self) -> tuple[str, int, int]:
        """The tracked line: ``(prefix, major, minor)``."""
        return (self.prefix, self.version.major, self.version.minor)


def _normalize_go(rest: str) -> str:
    """``1.27rc1`` -> ``1.27.0-rc.1``; anything else is returned unchanged."""
    m = _GO_RE.match(rest)
    if m is None or (m["kind"] is None and rest.count(".") == 2):
        return rest
    core = m["core"] if m["core"].count(".") == 2 else f"{m['core']}.0"
    return f"{core}-{m['kind']}.{m['num']}" if m["kind"] else core


def parse_ref(ref: str) -> ParsedRef | None:
    """Parse a tag like ``v1.14.6`` / ``v1.38.0-rc.1`` / ``go1.27rc1``.

    Returns ``None`` when the tag has no version, the version is not valid
    semver (after Go normalization), or it lacks a major.minor.
    """

    m = _REF_RE.match(ref.strip())
    if not m:
        return None

    prefix, rest = m["prefix"], m["rest"]
    if not _MAJOR_MINOR_RE.match(rest):
        return None
    if prefix == "go":
        rest = _normalize_go(rest)

    try:
        version = Version.parse(rest, optional_minor_and_patch=True)
    except ValueError:
        return None
    return ParsedRef(prefix=prefix, version=version, raw=ref)


def line_label(ref: str) -> str:
    """Human-readable tracked-line label, e.g. ``v1.14.6`` -> ``1.14.x``."""
    parsed = parse_ref(ref)
    if parsed is None:
        return ref
    return f"{parsed.version.major}.{parsed.version.minor}.x"


def _require(ref: str) -> ParsedRef:
    parsed = parse_ref(ref)
    if parsed is None:
        raise ValueError(f"not a parseable version tag: {ref!r}")
    return parsed


def select_new_tags(current_ref: str, candidates: Iterable[str]) -> list[str]:
    """Return candidate tags newer than ``current_ref`` on its tracked line.

    Filters to the same prefix + major.minor and strictly greater semver
    precedence (pre-releases included). Result is sorted oldest -> newest.

    Raises ``ValueError`` if ``current_ref`` itself is not a parseable tag (a
    bad baseline should fail loudly, not silently match nothing).
    """

    current = _require(current_ref)
    matched = [
        parsed
        for parsed in (parse_ref(tag) for tag in candidates)
        if parsed is not None and parsed.line == current.line and parsed.version > current.version
    ]
    matched.sort(key=lambda p: p.version)
    return [p.raw for p in matched]


def newest_tag(tags: Iterable[str]) -> str:
    """Return the highest-versioned tag from ``tags`` (assumes same line)."""
    parsed = [p for p in (parse_ref(t) for t in tags) if p is not None]
    if not parsed:
        raise ValueError("newest_tag called with no parseable tags")
    return max(parsed, key=lambda p: p.version).raw


def tag_sort_key(tag: str) -> tuple:
    """Sort key: oldest -> newest by semver; unparseable tags last, by name."""
    parsed = parse_ref(tag)
    return (parsed is None, parsed.version if parsed else Version(0), tag)
