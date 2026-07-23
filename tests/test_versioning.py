"""Version parsing + constraint matching tests (Milestone 2)."""

from __future__ import annotations

import pytest

from tracker.versioning import line_label, newest_tag, parse_ref, select_new_tags

# --- parse_ref -----------------------------------------------------------


def test_parse_plain_v_prefix():
    p = parse_ref("v1.14.6")
    assert p is not None
    assert p.prefix == "v"
    assert p.release == (1, 14, 6)
    assert p.prerelease is None
    assert p.line == ("v", 1, 14)


def test_parse_go_prefix():
    p = parse_ref("go1.26.5")
    assert p is not None
    assert p.prefix == "go"
    assert p.release == (1, 26, 5)


def test_parse_multi_dash_prefix():
    p = parse_ref("cluster-autoscaler-1.36.0")
    assert p is not None
    assert p.prefix == "cluster-autoscaler-"
    assert p.release == (1, 36, 0)
    assert p.prerelease is None


def test_parse_prerelease():
    p = parse_ref("v1.4.0-rc.1")
    assert p is not None
    assert p.release == (1, 4, 0)
    assert p.prerelease == "rc.1"


@pytest.mark.parametrize("bad", ["latest", "nightly", "release.r60", "", "v1", "vX.Y.Z"])
def test_parse_unparseable_returns_none(bad):
    assert parse_ref(bad) is None


# --- select_new_tags -----------------------------------------------------


def test_selects_only_newer_same_line():
    candidates = ["v1.14.5", "v1.14.6", "v1.14.7", "v1.14.8", "v1.15.0", "v2.0.0"]
    assert select_new_tags("v1.14.6", candidates) == ["v1.14.7", "v1.14.8"]


def test_result_sorted_oldest_to_newest():
    assert select_new_tags("v1.14.6", ["v1.14.9", "v1.14.7", "v1.14.8"]) == [
        "v1.14.7",
        "v1.14.8",
        "v1.14.9",
    ]


def test_skips_prereleases():
    candidates = ["v1.14.7", "v1.14.8-rc.1", "v1.14.8-beta.0", "v1.14.9-alpha.1"]
    assert select_new_tags("v1.14.6", candidates) == ["v1.14.7"]


def test_different_prefix_excluded():
    # A same-numbers tag on a different prefix is not on our line.
    assert select_new_tags("go1.26.5", ["v1.26.6", "go1.26.6"]) == ["go1.26.6"]


def test_multi_component_repo_line_isolation():
    # kubernetes/autoscaler carries many components; only cluster-autoscaler
    # tags on our minor qualify.
    candidates = [
        "cluster-autoscaler-1.36.0",
        "cluster-autoscaler-1.36.1",
        "cluster-autoscaler-1.37.0",
        "vertical-pod-autoscaler-1.4.0",
        "addon-resizer-1.8.20",
    ]
    assert select_new_tags("cluster-autoscaler-1.36.0", candidates) == [
        "cluster-autoscaler-1.36.1",
    ]


def test_quiet_when_no_newer_tags():
    assert select_new_tags("v1.14.6", ["v1.14.6", "v1.14.5", "v1.13.9"]) == []


def test_unparseable_candidates_ignored():
    assert select_new_tags("go1.26.5", ["weekly.2011-01-01", "go1.26.6", "go1.26"]) == ["go1.26.6"]


def test_bad_current_ref_raises():
    with pytest.raises(ValueError, match="parseable"):
        select_new_tags("not-a-version", ["v1.0.0"])


# --- helpers -------------------------------------------------------------


def test_line_label():
    assert line_label("v1.14.6") == "1.14.x"
    assert line_label("go1.26.5") == "1.26.x"
    assert line_label("cluster-autoscaler-1.36.0") == "1.36.x"


def test_newest_tag():
    assert newest_tag(["v1.14.7", "v1.14.9", "v1.14.8"]) == "v1.14.9"
