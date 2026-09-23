"""T2 — Presets motion Calme/Energie + progress-bar (spec #12, ticket #14).

Calme : punch-in doux 1.0 -> 1.12, transitions dissolve 0.22s.
Energie (defaut) : punch-in marque 1.0 -> 1.25, transitions whip 0.30-0.35s.

Mapping ffmpeg : le whip est un slide lateral rapide (xfade n'a pas de
transition "whip"), le dissolve un fondu enchaine (xfade "fade").
La progress-bar (hauteur 8px, fond blanc a 40%, premier plan couleur
highlight) est dessinee en surimpression, duree calculee apres assemblage.
"""

from __future__ import annotations

from typing import Any

# Hauteur spec de la progress-bar (pixels, video 1080x1920).
PROGRESS_BAR_HEIGHT = 8
# Fond de la barre : blanc a 40%.
PROGRESS_BAR_BACK_COLOR = "white@0.4"

MOTION_PRESETS: dict[str, dict[str, Any]] = {
    "calme": {
        "name": "calme",
        "punch_in_start": 1.0,
        "punch_in_end": 1.12,
        "transition": "fade",
        "transition_duration": 0.22,
    },
    "energie": {
        "name": "energie",
        "punch_in_start": 1.0,
        "punch_in_end": 1.25,
        "transition": "slideleft",
        "transition_duration": 0.30,
    },
}

DEFAULT_MOTION_PRESET = "energie"


def _normalize_name(name: str | None) -> str:
    return (name or "").strip().lower()


def get_motion_preset(name: str) -> dict[str, Any]:
    """Parametres d'un preset motion. Leve ValueError si inconnu."""
    key = _normalize_name(name)
    try:
        return MOTION_PRESETS[key]
    except KeyError:
        raise ValueError(
            f"unknown motion preset: {name!r} "
            f"(expected one of {sorted(MOTION_PRESETS)})"
        ) from None


def resolve_motion_preset(name: str | None) -> dict[str, Any]:
    """Parametres d'un preset motion, Energie par defaut (spec #12)."""
    key = _normalize_name(name)
    if not key:
        return MOTION_PRESETS[DEFAULT_MOTION_PRESET]
    return get_motion_preset(key)
