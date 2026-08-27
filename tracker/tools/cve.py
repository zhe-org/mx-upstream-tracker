"""CVE handling.

Two distinct concerns:

- :func:`find_cve_refs` (Milestone 5) — *detect* security-advisory references in
  free text (release notes, commit messages) while preflight builds the handoff
  bundle. Recognises multiple advisory schemes relevant to these repos: CVE,
  GitHub Security Advisories (GHSA), the Go vulnerability DB (GO), and Ubuntu
  Security Notices (USN). Pure regex, no network. The ``cve_*`` naming is kept
  for continuity even though the ids are not all CVEs.
- :func:`lookup` (Milestone 8, optional) — *enrich* a detected advisory with
  authoritative severity/description via NVD / GitHub Advisory, with caching and
  graceful degradation. Skeleton only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Advisory-id patterns. Each is matched case-insensitively; normalization to the
# scheme's canonical case happens in ``_normalize`` (GHSA keeps a lowercase
# suffix; the others are upper-cased).
#   CVE-YYYY-N...    (sequence 4+ digits)
#   GHSA-xxxx-xxxx-xxxx  (three base32-ish groups of four)
#   GO-YYYY-N...     (Go vulnerability database)
#   USN-N-N          (Ubuntu Security Notice)
_ADVISORY_RE = re.compile(
    r"CVE-\d{4}-\d{4,}"
    r"|GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}"
    r"|GO-\d{4}-\d{4,}"
    r"|USN-\d+-\d+",
    re.IGNORECASE,
)


def _normalize(match: str) -> str:
    """Canonicalise an advisory id's case: GHSA keeps a lowercase suffix.

    GitHub advisory ids are canonically ``GHSA-`` + a lowercase base32 suffix
    (e.g. ``GHSA-6vch-q96h-7gc3``); upper-casing them would break the
    ``github.com/advisories`` link. CVE / GO / USN are canonically upper-case.
    """

    if match[:5].upper() == "GHSA-":
        return "GHSA-" + match[5:].lower()
    return match.upper()


def find_cve_refs(text: str | Iterable[str]) -> list[str]:
    """Return sorted, de-duplicated advisory ids found in ``text``.

    Recognises CVE / GHSA / GO / USN ids. Accepts either a single string or an
    iterable of strings (e.g. a list of commit messages), which are joined
    before scanning.
    """

    blob = text if isinstance(text, str) else "\n".join(text)
    return sorted({_normalize(match.group(0)) for match in _ADVISORY_RE.finditer(blob)})


def lookup(cve_id: str) -> dict:
    """Return ``{cve, severity, summary}`` for an advisory id (or a stub on miss)."""
    raise NotImplementedError("Milestone 8: CVE lookup + caching")
