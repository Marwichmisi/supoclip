"""T4 — Sound design local : resolution des assets embarques (spec #12, #16).

Le rendu ne doit dependre que de fichiers locaux listes dans
``assets/audio/manifest.json``, chacun portant sa preuve de licence CC0
(URL + date + sha256). Un asset absent ou un manifeste introuvable ne doit
jamais faire echouer un rendu : la chaine audio se degrade, le clip sort.
"""

import hashlib
import json
import re
from pathlib import Path

import pytest

from src.media.sound import bed_seed, load_sound_library


def _write_asset(directory, name, payload=b"x"):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _manifest_entry(root, name, keywords, payload=b"x"):
    path = _write_asset(root / "audio", name, payload)
    rel = f"audio/{name}"
    return {
        "id": Path(rel).stem,
        "path": rel,
        "duration": 1.0,
        "license": "CC0 1.0 Universal",
        "source_url": "https://example.org/asset",
        "published_at": "2020-02-11",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "keywords": keywords,
    }


def test_shipped_bundle_resolves_beds_and_the_three_cue_kinds():
    library = load_sound_library()

    assert library.beds, "aucun lit musical embarque"
    for kind in ("whoosh", "pop", "rise"):
        assert library.sfx_for(kind), f"aucun SFX '{kind}' embarque"


def test_every_shipped_asset_exists_on_disk():
    library = load_sound_library()

    for asset in library.all_assets():
        assert asset.path.is_file(), f"{asset.id}: {asset.path} absent du disque"


def test_shipped_manifest_keeps_a_matching_sha256_for_every_asset():
    """La preuve de licence du spec #12 ne vaut que si le hash suit le fichier."""
    library = load_sound_library()

    for asset in library.all_assets():
        digest = hashlib.sha256(asset.path.read_bytes()).hexdigest()
        assert digest == asset.sha256, f"{asset.id}: sha256 ne correspond plus"


def test_shipped_assets_are_all_cc0():
    library = load_sound_library()

    for asset in library.all_assets():
        assert "cc0" in asset.license.lower()
        assert asset.source_url and asset.published_at


def test_shipped_publication_dates_are_real_dates():
    """`published_at` fait partie de la preuve de licence du spec #12 : une date
    inventeee la rendrait sans valeur. On verifie au minimum qu'elle existe et
    qu'elle n'est pas dans le futur — la precision (annee seule pour kenney.nl,
    qui ne publie que ca) est inscrite dans les notes du manifest."""
    from datetime import date

    today = date.today().isoformat()
    library = load_sound_library()

    for asset in library.all_assets():
        assert re.fullmatch(r"\d{4}(-\d{2}-\d{2})?", asset.published_at), (
            f"{asset.id}: date de publication illisible ({asset.published_at!r})"
        )
        assert asset.published_at <= today, f"{asset.id}: date de publication dans le futur"
        assert asset.source_url.startswith("https://"), f"{asset.id}: URL source non https"


def test_a_missing_asset_file_drops_only_that_asset(tmp_path):
    root = tmp_path / "assets"
    entries = [
        _manifest_entry(root, "bed/one.ogg", ["bed"]),
        _manifest_entry(root, "sfx/pop-1.ogg", ["sfx", "pop"]),
    ]
    (root / "audio" / "bed" / "one.ogg").unlink()
    manifest = root / "audio" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"version": 1, "kind": "audio", "assets": entries}),
        encoding="utf-8",
    )

    library = load_sound_library(manifest)

    assert library.beds == ()
    assert [asset.id for asset in library.sfx_for("pop")] == ["pop-1"]


def test_a_missing_manifest_yields_an_empty_library(tmp_path):
    library = load_sound_library(tmp_path / "absent.json")

    assert library.is_empty
    assert library.pick_bed("clip-1") is None
    assert library.pick_sfx("whoosh", 0) is None


def test_bed_and_sfx_selection_is_deterministic_and_spreads_across_seeds(tmp_path):
    root = tmp_path / "assets"
    entries = [
        _manifest_entry(root, "bed/one.ogg", ["bed"], b"one"),
        _manifest_entry(root, "bed/two.ogg", ["bed"], b"two"),
        _manifest_entry(root, "sfx/whoosh-a.ogg", ["sfx", "whoosh"], b"a"),
        _manifest_entry(root, "sfx/whoosh-b.ogg", ["sfx", "whoosh"], b"b"),
    ]
    manifest = root / "audio" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"version": 1, "kind": "audio", "assets": entries}),
        encoding="utf-8",
    )
    library = load_sound_library(manifest)

    assert library.pick_bed("clip-42") is library.pick_bed("clip-42")
    beds = {library.pick_bed(f"clip-{i}").id for i in range(40)}
    assert len(beds) == 2, "40 clips doivent couvrir tous les lits disponibles"

    assert library.pick_sfx("whoosh", 0) is library.pick_sfx("whoosh", 0)
    assert library.pick_sfx("whoosh", 0) is not library.pick_sfx("whoosh", 1)


def test_an_unknown_cue_kind_is_a_programming_error():
    library = load_sound_library()

    with pytest.raises(ValueError, match="unknown sound cue kind"):
        library.sfx_for("explosion")
    with pytest.raises(ValueError, match="unknown sound cue kind"):
        library.pick_sfx("explosion", 0)


def test_the_bed_of_a_clip_survives_its_re_renders():
    """Re-rendre un clip (variante de hook, captions) doit garder sa musique.

    Les noms de sortie portent un uuid a chaque passe : si la graine en dependait,
    comparer deux variantes de hook se ferait sur deux musiques differentes, et
    le choix affiche ne voudrait plus rien dire (story 7 de la spec #12).
    """
    source = Path("/clips/1712ab34.mp4")
    ranges = [(10.0, 18.5), (19.0, 24.0)]

    re_renders = {
        bed_seed(source, ranges),
        bed_seed(source, ranges),
        bed_seed(Path("/clips/1712ab34.mp4"), list(ranges)),
    }
    assert len(re_renders) == 1, "la graine bouge entre deux rendus du meme clip"


def test_different_clips_do_not_share_a_bed_seed():
    assert bed_seed(Path("/clips/a.mp4"), [(0.0, 5.0)]) != bed_seed(
        Path("/clips/b.mp4"), [(0.0, 5.0)]
    )
    assert bed_seed(Path("/clips/a.mp4"), [(0.0, 5.0)]) != bed_seed(
        Path("/clips/a.mp4"), [(6.0, 11.0)]
    )
