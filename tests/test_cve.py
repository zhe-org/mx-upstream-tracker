"""CVE reference detection tests (Milestone 5).

``find_cve_refs`` is the M5 detection piece used when preflight builds the
handoff bundle. It only *finds* references in text (release notes, commit
messages); authoritative severity/description enrichment is M8 (``lookup``).
"""

from __future__ import annotations

from tracker.tools.cve import find_cve_refs


def test_finds_and_dedupes_sorted():
    text = "Fixes CVE-2026-1111 and cve-2026-0009; see CVE-2026-1111 again."
    assert find_cve_refs(text) == ["CVE-2026-0009", "CVE-2026-1111"]


def test_no_refs_returns_empty():
    assert find_cve_refs("routine bugfixes, no security content") == []


def test_accepts_iterable_of_strings():
    assert find_cve_refs(["bump for CVE-2026-2222", "unrelated"]) == ["CVE-2026-2222"]


def test_normalizes_case_to_upper():
    assert find_cve_refs("cve-2026-3333") == ["CVE-2026-3333"]


def test_matches_long_numeric_suffix():
    # CVE ids can have more than 4 digits in the sequence number.
    assert find_cve_refs("CVE-2026-1234567") == ["CVE-2026-1234567"]


def test_finds_ghsa_ids_keeping_lowercase_suffix():
    # GitHub advisory ids are canonically GHSA- + a lowercase base32 suffix; the
    # prefix is upper-cased but the suffix must stay lowercase for the URL.
    assert find_cve_refs("See ghsa-6vch-q96h-7gc3 for details.") == ["GHSA-6vch-q96h-7gc3"]


def test_finds_go_vuln_ids():
    assert find_cve_refs("fixed by go-2024-1234") == ["GO-2024-1234"]


def test_finds_usn_ids():
    assert find_cve_refs("addressed in USN-1234-1") == ["USN-1234-1"]


def test_finds_mixed_schemes_sorted_and_deduped():
    text = "CVE-2026-1111, GHSA-6vch-q96h-7gc3, GO-2024-0001, USN-9-1, CVE-2026-1111"
    assert find_cve_refs(text) == [
        "CVE-2026-1111",
        "GHSA-6vch-q96h-7gc3",
        "GO-2024-0001",
        "USN-9-1",
    ]
