"""T6 — B-roll local : resolveur pur + plan (spec #12, ticket #17).

Comportement externe, jamais de details d'implementation : un mot-cle qui
matche donne un asset local, sinon rien (blur fallback) ; un asset sans
preuve de licence est refuse au chargement.
"""

import hashlib
import json
from pathlib import Path

import pytest

from src.media.broll_local import (
    BrollAsset,
    BrollLibrary,
    build_broll_plan,
    load_broll_library,
    normalize_broll_keyword,
    plan_broll_for_transcript,
    resolve_broll_for_keyword,
)


def _asset(asset_id="pixabay-rue-1", keywords=("rue", "ville")):
    return BrollAsset(
        id=asset_id,
        path=Path(f"/tmp/{asset_id}.mp4"),
        duration=8.0,
        license="CC0 1.0 Universal",
        source_url="https://pixabay.com/videos/123",
        published_at="2018-05-01",
        sha256="a" * 64,
        keywords=tuple(keywords),
    )


def test_normalize_ignores_case_and_accents():
    assert normalize_broll_keyword("Rue") == normalize_broll_keyword("rue")
    assert normalize_broll_keyword("forêt") == "foret"
    assert normalize_broll_keyword("  Ville! ") == "ville"


def test_exact_keyword_match_returns_first_manifest_asset():
    library = BrollLibrary([_asset("a", ("rue",)), _asset("b", ("rue",))])
    resolved = library.resolve("rue")
    assert resolved is not None
    assert resolved.id == "a"


def test_keyword_without_match_falls_back_to_blur():
    library = BrollLibrary([_asset("a", ("rue",))])
    assert library.resolve("ocean") is None


def test_partial_keyword_matches_local_stock():
    library = BrollLibrary([_asset("a", ("marche-ville",))])
    assert library.resolve("ville") is not None


def test_too_short_keyword_never_matches():
    library = BrollLibrary([_asset("a", ("rue",))])
    assert resolve_broll_for_keyword("x", library.all_assets()) is None
    assert resolve_broll_for_keyword("", library.all_assets()) is None


def test_plan_keeps_only_resolved_keywords_sorted_by_time():
    library = BrollLibrary([_asset("a", ("rue",)), _asset("b", ("foret",))])
    plan = build_broll_plan(
        [
            {"keyword": "ocean", "timestamp": 0.5, "duration": 3.0},
            {"keyword": "rue", "timestamp": 4.0, "duration": 3.0},
            {"keyword": "foret", "timestamp": 1.0, "duration": 2.5},
        ],
        library,
        clip_duration=12.0,
    )
    assert [cue.asset_id for cue in plan.cues] == ["b", "a"]
    assert [cue.keyword for cue in plan.cues] == ["foret", "rue"]


def test_plan_dedupes_same_asset_and_caps_overlays():
    library = BrollLibrary([_asset("a", ("rue", "ville"))])
    plan = build_broll_plan(
        [
            {"keyword": "rue", "timestamp": 1.0},
            {"keyword": "ville", "timestamp": 5.0},
        ],
        library,
        clip_duration=12.0,
        max_overlays=2,
    )
    assert len(plan.cues) == 1
    assert plan.cues[0].asset_id == "a"


def test_plan_accepts_fr_keys_and_mmss_timestamps():
    library = BrollLibrary([_asset("a", ("rue",))])
    plan = build_broll_plan(
        [{"mot_cle": "rue", "timestamp": "00:02", "duration": 3}],
        library,
        clip_duration=12.0,
    )
    assert len(plan.cues) == 1
    assert plan.cues[0].at == pytest.approx(2.0)


def test_transcript_fallback_finds_asset_keyword():
    library = BrollLibrary([_asset("a", ("rue",))])
    plan = plan_broll_for_transcript(
        "On marche dans la rue sous la pluie", library, clip_duration=10.0
    )
    assert len(plan.cues) == 1
    assert plan.cues[0].asset_id == "a"


def test_transcript_without_match_gives_empty_plan_for_blur():
    library = BrollLibrary([_asset("a", ("rue",))])
    plan = plan_broll_for_transcript(
        "On parle de cuisine italienne", library, clip_duration=10.0
    )
    assert plan.cues == []


def _write_broll_manifest(tmp_path: Path, assets):
    root = tmp_path / "assets"
    manifest = root / "broll" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"version": 1, "kind": "broll", "assets": assets}),
        encoding="utf-8",
    )
    return manifest


def _valid_entry(root: Path, name: str = "pixabay-123.mp4", content: bytes = b"broll-bytes"):
    blob = root / "assets" / "broll" / name
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(content)
    return {
        "id": "pixabay-123",
        "path": f"broll/{name}",
        "duration": 8.5,
        "license": "CC0",
        "source_url": "https://pixabay.com/videos/123",
        "published_at": "2018-05-01",
        "sha256": hashlib.sha256(content).hexdigest(),
        "keywords": ["rue", "ville"],
    }


def test_loader_accepts_valid_local_asset(tmp_path):
    manifest = _write_broll_manifest(tmp_path, [_valid_entry(tmp_path)])
    library = load_broll_library(manifest)
    assert not library.is_empty
    assert library.resolve("rue") is not None


def test_loader_skips_missing_file_toward_blur(tmp_path):
    entry = _valid_entry(tmp_path)
    # Fichier annonce mais jamais ecrit : le loader degrade vers le blur.
    (tmp_path / "assets" / "broll" / "pixabay-123.mp4").unlink()
    manifest = _write_broll_manifest(tmp_path, [entry])
    library = load_broll_library(manifest)
    assert library.is_empty


def test_loader_skips_hash_mismatch_toward_blur(tmp_path):
    entry = _valid_entry(tmp_path, content=b"octets-attestes")
    (tmp_path / "assets" / "broll" / "pixabay-123.mp4").write_bytes(b"autres-bytes")
    manifest = _write_broll_manifest(tmp_path, [entry])
    library = load_broll_library(manifest)
    assert library.is_empty


def test_loader_refuses_asset_without_license_proof(tmp_path):
    entry = _valid_entry(tmp_path)
    del entry["license"]
    manifest = _write_broll_manifest(tmp_path, [entry])
    library = load_broll_library(manifest)
    assert library.is_empty


def test_shipped_broll_manifest_loads():
    root = Path(__file__).resolve().parents[3]
    manifest = root / "assets" / "broll" / "manifest.json"
    assert manifest.exists()
    library = load_broll_library(manifest)
    assert isinstance(library, BrollLibrary)
