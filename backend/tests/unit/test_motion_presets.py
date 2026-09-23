"""T2 — Presets motion Calme/Energie (spec #12, ticket #14).

Calme : punch-in doux 1.0 -> 1.12, transitions dissolve 0.22s.
Energie (defaut) : punch-in marque 1.0 -> 1.25, transitions whip 0.30s.
Le whip est rendu par un slide lateral rapide (xfade n'a pas de "whip"),
le dissolve par un fondu enchaine (xfade "fade").
"""

import pytest

from src.motion_presets import (
    MOTION_PRESETS,
    PROGRESS_BAR_HEIGHT,
    get_motion_preset,
    resolve_motion_preset,
)


def test_catalogue_matches_spec_values():
    calme = MOTION_PRESETS["calme"]
    assert (calme["punch_in_start"], calme["punch_in_end"]) == (1.0, 1.12)
    assert calme["transition"] == "fade"
    assert calme["transition_duration"] == pytest.approx(0.22)

    energie = MOTION_PRESETS["energie"]
    assert (energie["punch_in_start"], energie["punch_in_end"]) == (1.0, 1.25)
    assert energie["transition"] == "slideleft"
    assert 0.30 <= energie["transition_duration"] <= 0.35


def test_progress_bar_height_matches_spec():
    assert PROGRESS_BAR_HEIGHT == 8


def test_unknown_preset_is_rejected():
    with pytest.raises(ValueError, match="unknown motion preset"):
        get_motion_preset("hype")


def test_resolve_defaults_to_energie():
    assert resolve_motion_preset(None)["name"] == "energie"
    assert resolve_motion_preset("calme")["name"] == "calme"
    assert resolve_motion_preset("  ENERGIE  ")["name"] == "energie"
    with pytest.raises(ValueError, match="unknown motion preset"):
        resolve_motion_preset("hype")
