"""Release-set selection tests (registries/ + per-set state).

A run targets one K8s release set, given in dot form (e.g. ``1.36``) and mapped
to a dash-form filename (``upstream-tracker-1-36.yaml`` / ``processed-1-36.json``).
"""

from __future__ import annotations

import pytest

from tracker import config, main

# --- config helpers ------------------------------------------------------


def test_slug_dot_to_dash():
    assert config.slug_for("1.36") == "1-36"
    assert config.slug_for("1.33") == "1-33"


def test_per_set_paths():
    assert config.registry_path_for("1.36") == config.REGISTRIES_DIR / "upstream-tracker-1-36.yaml"
    assert config.state_path_for("1.36") == config.STATE_DIR / "processed-1-36.json"


def test_release_set_env_drives_settings(monkeypatch):
    monkeypatch.delenv("TRACKER_PATH", raising=False)
    monkeypatch.delenv("STATE_PATH", raising=False)
    monkeypatch.setenv("RELEASE_SET", "1.34")
    settings = config.load_settings()
    assert settings.tracker_path.name == "upstream-tracker-1-34.yaml"
    assert settings.state_path.name == "processed-1-34.json"


# --- CLI argument --------------------------------------------------------


def test_available_release_sets_includes_seed():
    # The seed registry (1.36) ships in the repo.
    assert "1.36" in main._available_release_sets()


def test_missing_release_set_arg_errors():
    # argparse exits (code 2) when the required positional is absent.
    with pytest.raises(SystemExit):
        main.main([])


def test_invalid_release_set_format_errors(capsys):
    with pytest.raises(SystemExit) as exc:
        main.main(["1-36"])  # dash form is a filename detail, not valid input
    assert "Invalid release set" in str(exc.value)


def test_unknown_release_set_lists_available(capsys):
    with pytest.raises(SystemExit) as exc:
        main.main(["9.99"])
    message = str(exc.value)
    assert "No registry for release set" in message
    assert "1.36" in message  # available sets are surfaced
