"""Version parsing + constraint matching tests."""

from __future__ import annotations

import pytest

from tracker.versioning import line_label, newest_tag, parse_ref, select_new_tags

# --- parse_ref -----------------------------------------------------------


def test_parse_plain_v_prefix():
    p = parse_ref("v1.14.6")
    assert p is not None
    assert p.prefix == "v"
    assert str(p.version) == "1.14.6"
    assert p.line == ("v", 1, 14)


def test_parse_multi_dash_prefix():
    p = parse_ref("cluster-autoscaler-1.36.0")
    assert p is not None
    assert p.prefix == "cluster-autoscaler-"
    assert p.line == ("cluster-autoscaler-", 1, 36)


def test_parse_prerelease():
    p = parse_ref("v1.38.0-alpha.1")
    assert p is not None
    assert p.version.prerelease == "alpha.1"
    assert p.line == ("v", 1, 38)


@pytest.mark.parametrize(
    ("tag", "normalized"),
    [
        ("go1.26.5", "1.26.5"),
        ("go1.26", "1.26.0"),  # Go's first release of a line has no .0
        ("go1.27rc1", "1.27.0-rc.1"),
        ("go1.27beta2", "1.27.0-beta.2"),
    ],
)
def test_parse_go_spellings(tag, normalized):
    p = parse_ref(tag)
    assert p is not None
    assert p.prefix == "go"
    assert str(p.version) == normalized


@pytest.mark.parametrize(
    "bad", ["latest", "nightly", "release.r60", "", "v1", "vX.Y.Z", "v1.2.3.4", "v1.2.3-rc.01"]
)
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


def test_includes_prereleases_in_semver_order():
    candidates = ["v1.14.8", "v1.14.8-rc.1", "v1.14.7", "v1.14.8-beta.0", "v1.14.8-alpha.1"]
    assert select_new_tags("v1.14.6", candidates) == [
        "v1.14.7",
        "v1.14.8-alpha.1",
        "v1.14.8-beta.0",
        "v1.14.8-rc.1",
        "v1.14.8",
    ]


def test_prerelease_baseline_tracks_following_prereleases_and_release():
    # An edge branch sitting on an alpha keeps seeing alphas/betas/rcs and the GA.
    candidates = ["v1.38.0-alpha.1", "v1.38.0-alpha.2", "v1.38.0-rc.0", "v1.38.0", "v1.37.2"]
    assert select_new_tags("v1.38.0-alpha.1", candidates) == [
        "v1.38.0-alpha.2",
        "v1.38.0-rc.0",
        "v1.38.0",
    ]


def test_go_rc_ordered_before_go_release():
    assert select_new_tags("go1.27rc1", ["go1.27rc2", "go1.27", "go1.27.1", "go1.26.9"]) == [
        "go1.27rc2",
        "go1.27",
        "go1.27.1",
    ]


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
