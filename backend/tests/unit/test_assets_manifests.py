"""T1 — Socle assets: le chargeur de manifests valide la preuve de licence CC0.

Spec #12 : chaque asset embarque (audio, B-roll) doit garder sa preuve
de licence CC0 (URL + date + hash) dans son manifest, sous peine de
retirer le bundle du repo public.

Le chargeur accepte un manifest valide et rejette un manifest sans
preuve de licence. Il accepte les cles FR de la spec T6
(id, mots-cles, chemin, duree, licence, url source, date de publication)
ainsi que leurs equivalents EN.
"""

import json

import pytest

from src.assets_manifests import ManifestError, load_manifest


def _write_manifest(tmp_path, payload):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _valid_broll_asset():
    return {
        "id": "pixabay-123",
        "chemin": "broll/pixabay-123.mp4",
        "duree": 8.5,
        "licence": "CC0",
        "url_source": "https://pixabay.com/videos/123",
        "date_publication": "2018-05-01",
        "hash": "a" * 64,
        "mots-cles": ["rue", "ville"],
    }


def test_load_valid_broll_manifest_fr_keys(tmp_path):
    manifest = {
        "version": 1,
        "kind": "broll",
        "assets": [_valid_broll_asset()],
    }
    path = _write_manifest(tmp_path, manifest)
    loaded = load_manifest(path)
    assert loaded["kind"] == "broll"
    assert len(loaded["assets"]) == 1
    asset = loaded["assets"][0]
    assert asset["id"] == "pixabay-123"
    assert asset["path"] == "broll/pixabay-123.mp4"


def test_load_valid_manifest_en_keys(tmp_path):
    manifest = {
        "version": 1,
        "kind": "audio",
        "assets": [
            {
                "id": "kenney-whoosh-1",
                "path": "audio/whoosh-1.wav",
                "duration": 0.8,
                "license": "CC0",
                "source_url": "https://kenney.nl/assets/whoosh",
                "published_at": "2019-03-10",
                "sha256": "b" * 64,
                "keywords": ["whoosh", "cut"],
            }
        ],
    }
    path = _write_manifest(tmp_path, manifest)
    loaded = load_manifest(path)
    assert loaded["assets"][0]["license"] == "CC0"


def test_reject_manifest_without_license(tmp_path):
    asset = _valid_broll_asset()
    del asset["licence"]
    path = _write_manifest(tmp_path, {"version": 1, "kind": "broll", "assets": [asset]})
    with pytest.raises(ManifestError, match="licence"):
        load_manifest(path)


def test_reject_manifest_without_source_url(tmp_path):
    asset = _valid_broll_asset()
    del asset["url_source"]
    path = _write_manifest(tmp_path, {"version": 1, "kind": "broll", "assets": [asset]})
    with pytest.raises(ManifestError, match="source"):
        load_manifest(path)


def test_reject_manifest_without_publication_date(tmp_path):
    asset = _valid_broll_asset()
    del asset["date_publication"]
    path = _write_manifest(tmp_path, {"version": 1, "kind": "broll", "assets": [asset]})
    with pytest.raises(ManifestError, match="publication|date"):
        load_manifest(path)


def test_reject_manifest_without_hash(tmp_path):
    asset = _valid_broll_asset()
    del asset["hash"]
    path = _write_manifest(tmp_path, {"version": 1, "kind": "broll", "assets": [asset]})
    with pytest.raises(ManifestError, match="hash"):
        load_manifest(path)


def test_reject_non_cc0_license(tmp_path):
    asset = _valid_broll_asset()
    asset["licence"] = "All rights reserved"
    path = _write_manifest(tmp_path, {"version": 1, "kind": "broll", "assets": [asset]})
    with pytest.raises(ManifestError, match="CC0"):
        load_manifest(path)


def test_reject_manifest_missing_assets_list(tmp_path):
    path = _write_manifest(tmp_path, {"version": 1, "kind": "broll"})
    with pytest.raises(ManifestError, match="assets"):
        load_manifest(path)


def test_reject_manifest_without_kind(tmp_path):
    path = _write_manifest(
        tmp_path, {"version": 1, "assets": [_valid_broll_asset()]}
    )
    with pytest.raises(ManifestError, match="kind"):
        load_manifest(path)


def test_reject_broll_asset_without_keywords(tmp_path):
    asset = _valid_broll_asset()
    del asset["mots-cles"]
    path = _write_manifest(tmp_path, {"version": 1, "kind": "broll", "assets": [asset]})
    with pytest.raises(ManifestError, match="mots-cles"):
        load_manifest(path)


def test_shipped_manifests_load():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    for kind in ("audio", "broll"):
        manifest_path = root / "assets" / kind / "manifest.json"
        assert manifest_path.exists(), f"manifest manquant : {manifest_path}"
        loaded = load_manifest(str(manifest_path))
        assert loaded["kind"] == kind
        assert isinstance(loaded["assets"], list)
