"""T4 — Sound design local : le graphe de mixage audio (spec #12, ticket #16).

Le graphe est une chaine de filtres qui produit une seule piste audio :

    voix (jamais duckee) + lit musical duckee par sidechain sur la voix
    + SFXcales sur leurs timecodes  ->  loudnorm au standard plateforme

C'est du pur : a partir d'un plan et de fichiers locaux resolus, on obtient des
arguments ffmpeg. Aucune lecture de fichier, aucun appel reseau.
"""

import hashlib
import json
from pathlib import Path

from src.media.sound import (
    BED_GAIN_DB,
    LOUDNESS_TARGET_LUFS,
    SFX_GAIN_DB,
    SFX_FADE_IN,
    SFX_FADE_OUT,
    build_audio_mix_graph,
    build_sound_plan,
    load_sound_library,
)


def _library(tmp_path):
    """Bundle audio minimal : 1 lit, 1 whoosh, 1 pop, 1 rise."""
    root = tmp_path / "assets"
    entries = []
    for name, keywords, seconds in (
        ("bed/one.ogg", ["bed"], 8.0),
        ("sfx/whoosh-1.ogg", ["sfx", "whoosh"], 0.25),
        ("sfx/pop-1.ogg", ["sfx", "pop"], 0.12),
        ("sfx/rise-1.ogg", ["sfx", "rise"], 1.15),
    ):
        path = root / "audio" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
        entries.append({
            "id": Path(name).stem,
            "path": f"audio/{name}",
            "duration": seconds,
            "license": "CC0 1.0 Universal",
            "source_url": "https://example.org/asset",
            "published_at": "2020-02-11",
            "sha256": hashlib.sha256(name.encode()).hexdigest(),
            "keywords": keywords,
        })
    manifest = root / "audio" / "manifest.json"
    manifest.write_text(
        json.dumps({"version": 1, "kind": "audio", "assets": entries}),
        encoding="utf-8",
    )
    return load_sound_library(manifest)


def _graph(tmp_path, **kwargs):
    plan = build_sound_plan(
        keep_ranges=[(0.0, 4.0), (10.0, 14.0)],
        duration=7.78,
        words=[
            {"text": "10x", "start": 1.20, "end": 1.60},
            {"text": "20x", "start": 6.00, "end": 6.40},
        ],
        has_hook=True,
    )
    return build_audio_mix_graph(
        plan=plan, library=_library(tmp_path), seed="clip-1", **kwargs
    )


def test_the_voice_is_split_off_so_ducking_can_never_touch_it(tmp_path):
    mix = _graph(tmp_path)

    # sidechaincompress compresse SON premiere entree (le lit) avec la seconde
    # (la voix) comme cle de detection : la voix n'est ni duckee ni compressee.
    ducked = next(part for part in mix.graph.split(";") if "sidechaincompress" in part)
    compressed_input, key_input = ducked.split("sidechaincompress")[0].split("][")
    assert compressed_input == "[bedraw"
    assert key_input == "voicekey]"
    # La voix est donc routée deux fois : une vers le mix, une vers la cle.
    assert "[0:a]asplit=2[voice][voicekey]" in mix.graph
    assert "[voice]" in mix.graph


def test_the_music_bed_loops_for_the_whole_clip_and_sits_low(tmp_path):
    mix = _graph(tmp_path)

    # Le lit se repete pour couvrir un clip plus long que lui, et on l'attenue
    # avant ducking : le spec veut un lit "pre-normalise bas".
    assert mix.input_args[:2] == ["-stream_loop", "-1"]
    bed = next(
        part for part in mix.graph.split(";")
        if "atrim" in part and part.endswith("[bedraw]")
    )
    assert f"volume={BED_GAIN_DB}dB" in bed
    assert "atrim=0:7.780" in bed


def test_every_sfx_is_delayed_to_its_cue_and_faded_at_both_ends(tmp_path):
    mix = _graph(tmp_path)

    # pop sur le mot-cle du hook a 1.20s, whoosh sur le cut a 3.78s, rise 0.9s
    # avant la punchline de 6.00s.
    delayed = [part for part in mix.graph.split(";") if "adelay" in part]
    assert [
        next(fragment for fragment in part.split(",") if fragment.startswith("adelay")).split("[")[0]
        for part in delayed
    ] == ["adelay=1200:all=1", "adelay=3780:all=1", "adelay=5100:all=1"]

    for part in delayed:
        assert f"volume={SFX_GAIN_DB}dB" in part
        # Un SFX pose sans fondu d'entree/sortie fait un clic la.
        assert f"afade=t=in:st=0:d={SFX_FADE_IN}" in part
        assert "afade=t=out" in part
        assert f"d={SFX_FADE_OUT}" in part


def test_sfx_fades_come_before_the_delay(tmp_path):
    """`afade` raisonne sur l'horloge de sa piste : apres `adelay`, le fondu de
    sortie se finirait dans le silence de decalage etrasedirait le SFX."""
    mix = _graph(tmp_path)

    for part in (p for p in mix.graph.split(";") if "adelay" in p):
        assert part.index("afade") < part.index("adelay"), part


def test_loudness_normalisation_closes_the_chain_on_the_platform_standard(tmp_path):
    mix = _graph(tmp_path)

    # loudnorm est le dernier filtre : normaliser avant d'ajouter les SFX
    # laisserait les transients casser la cible.
    last = [part for part in mix.graph.split(";") if part][-1]
    assert last.endswith("[aout]")
    assert f"loudnorm=I={LOUDNESS_TARGET_LUFS}" in last
    # amix normalise a 1/N par defaut, ce qui affaiblirait le lit comme la
    # voix. On desactive la normalisation et on laisse loudnorm regler le
    # gain final.
    assert "amix=inputs=5:normalize=0" in mix.graph


def test_measured_loudness_is_forwarded_to_the_final_pass(tmp_path):
    measured = {
        "measured_I": -18.4,
        "measured_TP": -3.1,
        "measured_LRA": 6.2,
        "measured_thresh": -28.9,
        "offset": 0.4,
    }

    mix = _graph(tmp_path, measured=measured)

    last = [part for part in mix.graph.split(";") if part][-1]
    for key, value in measured.items():
        assert f"{key}={value}" in last


def test_every_input_is_an_existing_local_file(tmp_path):
    mix = _graph(tmp_path)

    inputs = [
        mix.input_args[index + 1]
        for index, value in enumerate(mix.input_args)
        if value == "-i"
    ]
    assert inputs
    for value in inputs:
        candidate = Path(value)
        assert candidate.is_file(), f"{value} n'est pas un fichier local"


def test_no_mix_graph_when_the_bundle_plays_nothing(tmp_path):
    plan = build_sound_plan(keep_ranges=[(0.0, 4.0), (10.0, 14.0)], duration=7.78)

    empty = load_sound_library(tmp_path / "absent.json")

    assert build_audio_mix_graph(plan=plan, library=empty, seed="clip-1") is None
