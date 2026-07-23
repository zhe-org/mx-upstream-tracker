"""Version / tag parsing and constraint matching (Milestone 2).

The orchestrator only cares about upstream tags that are *newer than* our
``current_upstream_tag`` **on the same tracked line** implied by it. The tracked
line is the ``(prefix, major, minor)`` of the current tag; e.g.

    v1.14.6                     -> track v1.14.*   (prefix "v")
    go1.26.5                    -> track go1.26.*  (prefix "go")
    cluster-autoscaler-1.36.0   -> track cluster-autoscaler-1.36.*

A tag qualifies when it shares the same prefix and major.minor and has a
strictly greater release version. Pre-release tags (``-rc``/``-alpha``/
``-beta`` and any build metadata) are skipped — we only track stable patch
releases. Unparseable tags are ignored.

This module is pure and deterministic (no I/O), so it is trivially testable and
kept out of the LLM path.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# Leading non-digits form the prefix; the rest starts at the first digit.
_REF_RE = re.compile(r"^(?P<prefix>\D*)(?P<rest>\d.*)$")


@dataclass(frozen=True)
class ParsedRef:
    """A tag decomposed into its comparable parts."""

    prefix: str
    release: tuple[int, ...]
    prerelease: str | None
    raw: str

    @property
    def line(self) -> tuple[str, int, int]:
        """The tracked line: ``(prefix, major, minor)``."""
        return (self.prefix, self.release[0], self.release[1])


def parse_ref(ref: str) -> ParsedRef | None:
    """Parse a tag like ``v1.14.6`` / ``go1.26.5`` / ``cluster-autoscaler-1.36.0``.

    Returns ``None`` when the tag has no numeric version, a non-integer version
    component, or fewer than two components (no major.minor to form a line).
    """

    m = _REF_RE.match(ref.strip())
    if not m:
        return None

    prefix = m.group("prefix")
    rest = m.group("rest")

    # Split the numeric core from any pre-release / build metadata, on the first
    # '-' or '+'. (Prefix dashes, e.g. "cluster-autoscaler-", are already
    # absorbed into ``prefix`` because they precede the first digit.)
    cut = min((i for i in (rest.find("-"), rest.find("+")) if i != -1), default=-1)
    if cut != -1:
        core, prerelease = rest[:cut], rest[cut + 1 :]
    else:
        core, prerelease = rest, None

    try:
        release = tuple(int(part) for part in core.split("."))
    except ValueError:
        return None

    if len(release) < 2:
        return None

    return ParsedRef(prefix=prefix, release=release, prerelease=prerelease or None, raw=ref)


def line_label(ref: str) -> str:
    """Human-readable tracked-line label, e.g. ``v1.14.6`` -> ``1.14.x``."""
    parsed = parse_ref(ref)
    if parsed is None:
        return ref
    return f"{parsed.release[0]}.{parsed.release[1]}.x"


def select_new_tags(current_ref: str, candidates: Iterable[str]) -> list[str]:
    """Return candidate tags newer than ``current_ref`` on its tracked line.

    Filters to the same prefix + major.minor, strictly greater release, stable
    only (pre-releases skipped). Result is sorted oldest -> newest.

    Raises ``ValueError`` if ``current_ref`` itself is not a parseable tag with
    a major.minor (a malformed registry should fail loudly, not silently match
    nothing).
    """

    current = parse_ref(current_ref)
    if current is None:
        raise ValueError(f"current_upstream_tag is not a parseable version tag: {current_ref!r}")

    line = current.line
    matched: list[ParsedRef] = []
    for tag in candidates:
        parsed = parse_ref(tag)
        if parsed is None or parsed.prerelease is not None:
            continue
        if parsed.line != line:
            continue
        if parsed.release <= current.release:
            continue
        matched.append(parsed)

    matched.sort(key=lambda p: p.release)
    return [p.raw for p in matched]


def newest_tag(tags: Iterable[str]) -> str:
    """Return the highest-versioned tag from ``tags`` (assumes same line)."""
    parsed = [p for p in (parse_ref(t) for t in tags) if p is not None]
    if not parsed:
        raise ValueError("newest_tag called with no parseable tags")
    return max(parsed, key=lambda p: p.release).raw
