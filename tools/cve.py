"""CVE enrichment (Milestone 8, optional).

Look up CVE metadata (severity, description) via NVD / GitHub Advisory to
enrich dependency + release findings. Degrades gracefully when unavailable.
Skeleton only.
"""

from __future__ import annotations


def lookup(cve_id: str) -> dict:
    """Return ``{cve, severity, summary}`` for a CVE id (or a stub on miss)."""
    raise NotImplementedError("Milestone 8: CVE lookup + caching")
