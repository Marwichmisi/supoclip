"""T1 — Socle assets : chargeur de manifests avec preuve de licence CC0.

Chaque asset embarque (audio, B-roll) doit garder sa preuve de licence
CC0 (URL + date + hash) dans son manifest, sous peine de retirer le
bundle du repo public (spec #12).

Le chargeur accepte les cles FR de la spec T6
(id, mots-cles, chemin, duree, licence, url source, date de publication)
ainsi que leurs equivalents EN, et normalise vers des cles EN.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ManifestError(ValueError):
    """Manifest invalide ou preuve de licence manquante."""


MANIFEST_FILENAME = "manifest.json"

_VALID_KINDS = {"audio", "broll"}


def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _normalize_asset(raw: dict[str, Any], index: int, kind: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ManifestError(f"asset #{index} : objet JSON attendu")

    asset_id = _first_present(raw, "id")
    path = _first_present(raw, "path", "chemin")
    duration = _first_present(raw, "duration", "duree")
    license_value = _first_present(raw, "license", "licence")
    source_url = _first_present(
        raw, "source_url", "sourceUrl", "url_source", "url source", "url-source"
    )
    published_at = _first_present(
        raw,
        "published_at",
        "publishedAt",
        "date_publication",
        "date-publication",
        "date publication",
    )
    hash_value = _first_present(raw, "sha256", "hash")
    keywords = _first_present(
        raw, "keywords", "mots_cles", "mots-cles", "mots_clés", "mots cles"
    )

    label = asset_id if asset_id else f"#{index}"
    if not asset_id:
        raise ManifestError(f"asset #{index} : 'id' manquant")
    if not path:
        raise ManifestError(f"asset {label} : 'chemin'/'path' manquant")
    if duration is None:
        raise ManifestError(f"asset {label} : 'duree'/'duration' manquante")
    try:
        duration_value = float(duration)
    except (TypeError, ValueError):
        raise ManifestError(f"asset {label} : 'duree'/'duration' invalide") from None
    if duration_value < 0:
        raise ManifestError(f"asset {label} : 'duree'/'duration' negative")
    if not license_value:
        raise ManifestError(f"asset {label} : preuve de licence manquante ('licence')")
    if "cc0" not in str(license_value).lower():
        raise ManifestError(f"asset {label} : licence non-CC0 ({license_value!r})")
    if not source_url:
        raise ManifestError(
            f"asset {label} : preuve de licence manquante "
            "('url source'/'source_url')"
        )
    if not published_at:
        raise ManifestError(
            f"asset {label} : preuve de licence manquante "
            "('date de publication'/'published_at')"
        )
    if not hash_value:
        raise ManifestError(f"asset {label} : preuve de licence manquante ('hash')")

    normalized_keywords: list[str] = []
    if keywords is not None:
        if isinstance(keywords, str):
            normalized_keywords = [keywords]
        elif isinstance(keywords, list):
            normalized_keywords = [str(k) for k in keywords]
        else:
            raise ManifestError(f"asset {label} : 'mots-cles' invalide")
    if kind == "broll" and not normalized_keywords:
        raise ManifestError(f"asset {label} : 'mots-cles' requis pour le B-roll")

    return {
        "id": str(asset_id),
        "path": str(path),
        "duration": duration_value,
        "license": str(license_value),
        "source_url": str(source_url),
        "published_at": str(published_at),
        "sha256": str(hash_value),
        "keywords": normalized_keywords,
    }


def load_manifest(path: str | Path) -> dict[str, Any]:
    """Charge et valide un manifest JSON. Rejette sans preuve de licence."""
    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ManifestError(f"manifest introuvable : {manifest_path}") from None
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest JSON invalide : {exc}") from None

    if not isinstance(payload, dict):
        raise ManifestError("manifest : objet JSON attendu a la racine")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ManifestError("manifest : liste 'assets' manquante")
    kind = str(payload.get("kind", "")).lower()
    if kind not in _VALID_KINDS:
        raise ManifestError(
            f"manifest : 'kind' manquant ou inconnu ({payload.get('kind')!r}), "
            "attendu 'audio' ou 'broll'"
        )
    version = payload.get("version", 1)

    normalized = [
        _normalize_asset(raw, index, kind) for index, raw in enumerate(assets)
    ]
    return {"version": version, "kind": kind, "assets": normalized}


def assets_root() -> Path:
    """Racine versionnee des assets locaux (repo/assets)."""
    return Path(__file__).resolve().parents[2] / "assets"


def manifest_path_for(kind: str) -> Path:
    """Chemin du manifest versionne pour un type d'asset ('audio' | 'broll')."""
    normalized_kind = kind.lower()
    if normalized_kind not in _VALID_KINDS:
        raise ManifestError(f"type d'asset inconnu : {kind!r}")
    return assets_root() / normalized_kind / MANIFEST_FILENAME
