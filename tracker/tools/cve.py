"""CVE handling.

Two distinct concerns:

- :func:`find_cve_refs` (Milestone 5) — *detect* CVE references in free text
  (release notes, commit messages) while preflight builds the handoff bundle.
  Pure regex, no network.
- :func:`lookup` (Milestone 8, optional) — *enrich* a detected CVE with
  authoritative severity/description via NVD / GitHub Advisory, with caching and
  graceful degradation. Skeleton only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# CVE ids look like CVE-YYYY-N... where the sequence number is 4+ digits.
_CVE_RE = re.compile(r"CVE-\d{4}-\d{4,}", re.IGNORECASE)


def find_cve_refs(text: str | Iterable[str]) -> list[str]:
    """Return sorted, de-duplicated, upper-cased CVE ids found in ``text``.

    Accepts either a single string or an iterable of strings (e.g. a list of
    commit messages), which are joined before scanning.
    """

    blob = text if isinstance(text, str) else "\n".join(text)
    return sorted({match.group(0).upper() for match in _CVE_RE.finditer(blob)})


def lookup(cve_id: str) -> dict:
    """Return ``{cve, severity, summary}`` for a CVE id (or a stub on miss)."""
    raise NotImplementedError("Milestone 8: CVE lookup + caching")
