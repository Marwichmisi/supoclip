"""T4 — Sound design local : comportement externe du rendu (spec #12, #16).

Les trois autres fichiers `test_sound_*` decrivent le contrat interne (manifest,
timecodes, chaine de filtres). Ici on ne mesure que ce qu'un createur entend et
ce qu'une plateforme tolere, sur un vrai rendu ffmpeg :

- le lit musical s'entend dans les creux et **s'efface** sous la parole ;
- la voix, elle, ne bouge pas d'un dB quand le ducking s'engage ;
- les SFX sont la au bon timecode, avec des fondus anti-pop ;
- le mix sort au niveau des plateformes courtes ;
- aucun octet ne sort du disque local : le rendu ne fait pas de reseau.

Deux sources synthetiques, chacune avec un role, pour que les mesures ne se
contaminent pas :

- `voiced` : une « voix » de 6 kHz gatee (1.2s de parole / 0.8s de silence).
  Bandes disjointes du lit (200-1500 Hz) et des SFX (1.2-3.5 kHz), donc on lit
  le lit, la voix et les SFX separement dans le meme mix.
- `muted`  : la meme video avec une piste audio numeriquement muette. Le mix
  n'y contient que les SFX : le silence de reference est donc vrai zéro, ce qui
  permet de mesurer un SFX a -inf dBFS et de juger ses bords sans que la voix
  ne masque un eventuel clic.
"""

import hashlib
import json
import re
import shutil
import socket
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from src.media import sound as sound_module
from src.media.reframing import render_reframed_clip_ffmpeg
from src.media.sound import (
    SFX_FADE_OUT,
    ClipSound,
    SoundLibrary,
    build_audio_mix_graph,
    build_sound_plan,
    load_sound_library,
    measure_loudness,
    mix_graph_for,
    prepare_clip_sound,
)

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg required",
)

RATE = 48000
VOICE_HZ = 6000.0
SECONDS = 12.0
#: 1.2s de parole puis 0.8s de silence, en boucle : la source offre a la fois
#: des creux (pour ecouter le lit) et de la parole (pour le mesurer ducke).
GATE_ON = 1.2
GATE_PERIOD = 2.0

#: Bande de mesure du lit, et de la voix. Les deux ne se recouvrent pas, et
#: aucune ne touche la bande des SFX : c'est ce qui permet de lire le lit et la
#: voix separement dans le meme rendu. On mesure le lit entre 1.5 et 5 kHz parce
#: que c'est la ou tous les lits livres ont de l'energie — une bande grave ne
#: verrait qu'un de nos lits et laisserait les deux autres dans le vide.
BED_BAND = (1500.0, 5000.0)
VOICE_BAND = (5500.0, 6500.0)

#: Fenetres de mesure, alignees sur le fenetrage de `voiced` ci-dessus. Elles
#: tombent dans le silence de la source : le mix n'y contient que le lit.
GAP_WINDOWS = [(1.35, 0.5), (3.35, 0.5), (5.35, 0.5), (7.35, 0.5), (9.35, 0.5)]
SPEECH_WINDOWS = [(0.35, 0.5), (2.35, 0.5), (4.35, 0.5), (6.35, 0.5), (8.35, 0.5)]


def windows(within: float):
    """Les fenetres de mesure qui tiennent dans un rendu de `within` secondes."""
    gaps = [(at, dur) for at, dur in GAP_WINDOWS if at + dur <= within]
    speech = [(at, dur) for at, dur in SPEECH_WINDOWS if at + dur <= within]
    assert gaps and speech, f"aucune fenetre de mesure sous {within}s"
    return gaps, speech


#: Bande propre a chaque SFX du bundle de test. Aucune ne chevauche la bande du
#: lit (1.5-5 kHz) ni celle de la voix (5.5-6.5 kHz) : sinon un SFX contaminerait
#: la mesure du ducking, et le test mesurerait autre chose que ce qu'il pretend.
SFX_BANDS = {
    "whoosh": (7500.0, 8500.0),
    "pop": (8500.0, 9500.0),
    "rise": (6500.0, 7500.0),
}


# --- fabrique de fixtures ------------------------------------------------------


def _write_wav(path: Path, hz: float, seconds: float, amplitude: float = 0.5,
               ramp: bool = True) -> Path:
    """Un WAV mono : ton pur. `ramp` adoucit les bords (lit, source) ; a false,
    les bords sont francs — c'est alors au fondu du graphe d'eliminer le clic,
    ce qu'on veut justement mesurer sur les SFX.
    """
    samples = np.arange(int(RATE * seconds)) / RATE
    wave_data = amplitude * np.sin(2 * np.pi * hz * samples)
    if ramp:
        edge = max(1, int(RATE * 0.005))
        cosine = 0.5 * (1 - np.cos(np.pi * np.arange(edge) / edge))
        wave_data[:edge] *= cosine
        wave_data[-edge:] *= cosine[::-1]
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes((np.clip(wave_data, -1, 1) * 32767).astype(np.int16).tobytes())
    return path


def _write_voice(path: Path, seconds: float = SECONDS) -> Path:
    """La « voix » : 6 kHz gates par cycles de 1.2s, sans clic aux transitions."""
    samples = np.arange(int(RATE * seconds)) / RATE
    voice = 0.6 * np.sin(2 * np.pi * VOICE_HZ * samples)
    voice *= (samples % GATE_PERIOD < GATE_ON).astype(np.float32)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes((np.clip(voice, -1, 1) * 32767).astype(np.int16).tobytes())
    return path


def _write_silence(path: Path, seconds: float = SECONDS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(np.zeros(int(RATE * seconds), dtype=np.int16).tobytes())
    return path


def _mux(tmp_path: Path, audio: Path, name: str, seconds: float = SECONDS) -> Path:
    """Clip de test : image de repos + piste audio, encodes en H.264/AAC."""
    out = tmp_path / name
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=24:duration={seconds}",
            "-i", str(audio),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", str(RATE), "-ac", "1",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


def _bundle(root: Path, assets) -> Path:
    """Bundle audio de test : ecrit les fichiers et leur manifest versionne."""
    entries = []
    for name, keywords, seconds, hz, ramp in assets:
        payload = _write_wav(root / "audio" / name, hz, seconds, ramp=ramp).read_bytes()
        entries.append(
            {
                "id": Path(name).stem,
                "path": f"audio/{name}",
                "duration": seconds,
                "license": "CC0 1.0 Universal",
                "source_url": "https://example.org/asset",
                "published_at": "2020-02-11",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "keywords": keywords,
            }
        )
    manifest = root / "audio" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"version": 1, "kind": "audio", "assets": entries}), encoding="utf-8"
    )
    return manifest


#: (chemin, mots-cles, duree, tone, bords adoucis)
BED_ASSET = ("bed/one.wav", ["bed"], 4.0, 2500.0, True)
SFX_ASSETS = [
    # Les SFX du test ont des bords francs : sans le fondu du graphe, on
    # entendrait un clic la, et le test le verrait. Leur tone est au-dessus de
    # la bande du lit et de celle de la voix, pour ne salir aucune mesure.
    ("sfx/whoosh-1.wav", ["sfx", "whoosh"], 0.25, 8000.0, False),
    ("sfx/pop-1.wav", ["sfx", "pop"], 0.10, 9000.0, False),
    ("sfx/rise-1.wav", ["sfx", "rise"], 1.15, 7000.0, False),
]

#: Deux coupes dans le plan : whoosh au cut. Le pop tombe sur le mot-cle du
#: hook, le rise 0.9s avant la punchline. Les trois survivent a l'ecartement
#: minimal entre cues (0.6s).
KEEP_RANGES = [(0.0, 6.0), (6.0, 12.0)]
PLAN_DURATION = 11.78
HOOK_WORDS = [
    {"text": "90%", "start": 2.0, "end": 2.4},
    {"text": "tu", "start": 8.2, "end": 8.5},
    {"text": "gagnes", "start": 8.5, "end": 8.9},
    {"text": "10x", "start": 8.9, "end": 9.5},
]


def _plan_and_library(tmp_path: Path, name: str = "bundle", *, bed=True):
    """Le plan du clip et le bundle qui le realise."""
    assets = ([BED_ASSET] if bed else []) + SFX_ASSETS
    library = load_sound_library(_bundle(tmp_path / name, assets))
    plan = build_sound_plan(
        keep_ranges=KEEP_RANGES,
        duration=PLAN_DURATION,
        words=HOOK_WORDS,
        has_hook=True,
    )
    return plan, library


@pytest.fixture(scope="module")
def voiced(tmp_path_factory):
    """Source avec une voix gatee : pour mesurer le lit et le ducking."""
    tmp = tmp_path_factory.mktemp("voiced")
    return _mux(tmp, _write_voice(tmp / "voice.wav"), "voiced.mp4")


@pytest.fixture(scope="module")
def muted(tmp_path_factory):
    """Source a piste muette : pour mesurer les SFX seuls, sans concurrent."""
    tmp = tmp_path_factory.mktemp("muted")
    return _mux(tmp, _write_silence(tmp / "silence.wav"), "muted.mp4")


# --- mesures -------------------------------------------------------------------


def _decode(path: Path) -> np.ndarray:
    wav = path.with_name(path.stem + "-pcm.wav")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(path), "-c:a", "pcm_s16le",
         "-ar", str(RATE), "-ac", "1", str(wav)],
        check=True,
        capture_output=True,
    )
    with wave.open(str(wav), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def _band_db(samples: np.ndarray, band, at: float, duration: float) -> float:
    """RMS en dBFS de la signal contenu dans `band`, sur une fenetre.

    La fenetre de Hann necessite une compensation : sans elle, on lit l'energie
    de `x * w`, soit 3/8 de celle de `x`, et tous les chiffres absolus sont
    4.3 dB trop bas. Diviser par la racine de la moyenne des carres de la
    fenetre rend la mesure comparable a un niveau dBFS reel.

    La bande sert a separer le signal etudie de l'autre source du mix : c'est ce
    qui permet d'affirmer « le lit s'efface » et « la voix ne bouge pas » sur le
    meme rendu.
    """
    low, high = band
    segment = samples[int(at * RATE):int((at + duration) * RATE)]
    assert len(segment) >= 64, f"fenetre vide a {at}s"
    window = np.hanning(len(segment))
    spectrum = np.abs(np.fft.rfft(segment * window))
    freqs = np.fft.rfftfreq(len(segment), 1 / RATE)
    in_band = spectrum[(freqs >= low) & (freqs < high)]
    rms = float(np.sqrt((in_band**2).sum()) / len(segment) / np.sqrt((window**2).mean()))
    return 20 * np.log10(rms + 1e-12)


def _rms_db(samples: np.ndarray, windows) -> float:
    """RMS en dBFS de tout le signal, moyenne sur des fenetres.

    Sans separation de frequences, c'est le bon outil la ou une seule source
    parle : dans un creux de la voix, le mix *est* le lit ; sous la parole, le
    mix *est* la voix. C'est ce qui permet de mesurer l'ecart entre les deux
    sans choisir de bande.
    """
    values = []
    for at, duration in windows:
        segment = samples[int(at * RATE):int((at + duration) * RATE)]
        assert len(segment) >= 64, f"fenetre vide a {at}s"
        values.append(20 * np.log10(np.sqrt((segment**2).mean()) + 1e-12))
    return float(np.mean(values))


def _mean_band_db(samples: np.ndarray, band, windows) -> float:
    return float(np.mean([_band_db(samples, band, at, dur) for at, dur in windows]))


def _loudness(path: Path) -> dict:
    """Loudness integree, true peak et plage LRA du fichier.

    `peak=true` fait sortir la section « True Peak » d'ebur128 : c'est un peak
    sur-echantillonne, donc bien le dBTP que `loudnorm`'s `TP` promet de tenir.
    """
    report = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stderr
    # ebur128 emet un « Summary: » final *et* une ligne de suivi par fenetre.
    # Seule la premiere ligne du resume donne la mesure du fichier entier.
    summary = report[report.rfind("Summary:"):]
    assert "True peak" in summary, f"ebur128 n'a pas mesure le true peak : {summary[-300:]}"
    integrated = re.search(r"I:\s+(-?\d+\.?\d*) LUFS", summary)
    lra = re.search(r"LRA:\s+(-?\d+\.?\d*) LU", summary)
    peak = re.search(r"Peak:\s+(-?\d+\.?\d*) dBFS", summary)
    assert integrated and lra and peak, f"mesure ebur128 inexploitable : {summary[-300:]}"
    return {
        "i": float(integrated.group(1)),
        "lra": float(lra.group(1)),
        "tp": float(peak.group(1)),
    }


def _render_mix(source: Path, plan, library, out: Path, seed: str = "clip-1") -> np.ndarray:
    """Rend le graphe de mixage en WAV, par le meme chemin que la passe finale.

    On passe par ``measure_loudness`` et non par une reconstruction de la
    mesure : ce qui est teste est le chemin de production, y compris le repli en
    mode dynamique quand la mesure n'est pas exploitable.
    """
    sound = ClipSound(plan=plan, library=library, seed=seed)
    mix = mix_graph_for(sound, measure_loudness(source, sound))
    assert mix is not None, "le bundle devrait produire un mix"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(source), *mix.input_args,
         "-filter_complex", mix.graph, *mix.map_args, "-c:a", "pcm_s16le", str(out)],
        check=True,
        capture_output=True,
    )
    return _decode(out)


def _cues(plan):
    return {cue.kind: cue.at for cue in plan.cues}


# --- AC 1 : le lit musical s'entend et s'efface sous la voix -------------------


def test_the_music_bed_is_audible_and_ducks_under_the_voice(tmp_path, voiced):
    """Le lit s'entend dans les creux, et s'ecarte quand la parole commence.

    Les deux mesures se font sans separation de frequences, donc sans avoir a
    choisir une bande : dans un creux de la source le mix ne contient que le
    lit, et sous la parole que la voix. C'est exactement le regard d'un mixeur.
    """
    plan, library = _plan_and_library(tmp_path, bed=True)
    assert library.beds, "le bundle de test doit embarquer un lit"

    mixed = _render_mix(voiced, plan, library, tmp_path / "ducked.wav")
    bed_level = _rms_db(mixed, GAP_WINDOWS)
    voice_level = _rms_db(mixed, SPEECH_WINDOWS)

    # Le lit se tient sous la parole sans disparaitre : 10 a 22 dB d'ecart, la
    # fourchette d'un lit de musique discret mais audible. Trop bas, on
    # n'entend rien ; trop haut, on ne comprend plus les mots.
    offset = voice_level - bed_level
    assert 10.0 <= offset <= 22.0, f"lit a {offset:.1f} dB sous la voix (creux {bed_level:.1f})"

    # ... et il s'ecarte reellement des que la parole commence. La recherche #4
    # en demande 6 dB ; on l'exige, ici, sur le signal.
    in_gap = _mean_band_db(mixed, BED_BAND, GAP_WINDOWS)
    under_speech = _mean_band_db(mixed, BED_BAND, SPEECH_WINDOWS)
    assert in_gap - under_speech >= 6.0, f"ducking trop faible ({in_gap - under_speech:.1f} dB)"


def test_the_bed_is_what_carries_those_low_frequencies(tmp_path, voiced):
    """Temoin : sans lit dans le bundle, la bande du lit est vide. Sans ce test,
    la mesure du ducking pourrait ne voir que du bruit de source."""
    with_bed = _plan_and_library(tmp_path, "bundle-avec-lit", bed=True)
    without_bed = _plan_and_library(tmp_path, "bundle-sans-lit", bed=False)

    lit = _render_mix(voiced, *with_bed, tmp_path / "a.wav")
    nu = _render_mix(voiced, *without_bed, tmp_path / "b.wav")

    lit_db = _mean_band_db(lit, BED_BAND, GAP_WINDOWS)
    nu_db = _mean_band_db(nu, BED_BAND, GAP_WINDOWS)
    assert lit_db - nu_db > 20.0, f"le lit ne change rien au rendu ({lit_db - nu_db:.1f} dB)"


def test_every_shipped_bed_sits_audibly_under_the_voice(tmp_path, voiced):
    """Le meme regard, sur les lits CC0 reellement livres — pas seulement sur nos
    fixtures. Un lit embarque trop fort ou trop faible passerait tous les autres
    tests : ceux-ci ne mesurent que le bundle de test.

    On mesure le lit seul, sans SFX : les whoosh/pop/rise livres sont larges
    bande et tomberaient dans la bande de mesure du lit.
    """
    library = load_sound_library()
    assert library.beds, "aucun lit musical embarque"
    gaps, speech = windows(SECONDS)
    plan = build_sound_plan(
        keep_ranges=KEEP_RANGES, duration=PLAN_DURATION, has_hook=False
    )

    for bed in library.beds:
        # Une bibliotheque par lit : on veut la mesure de *ce* lit, pas du bundle.
        mixed = _render_mix(
            voiced, plan, SoundLibrary([bed]), tmp_path / f"{bed.id}.wav", seed="clip-1"
        )

        offset = _rms_db(mixed, speech) - _rms_db(mixed, gaps)
        assert 10.0 <= offset <= 22.0, f"{bed.id} : lit a {offset:.1f} dB sous la voix"

        depth = (
            _mean_band_db(mixed, BED_BAND, gaps)
            - _mean_band_db(mixed, BED_BAND, speech)
        )
        assert depth >= 6.0, f"{bed.id} : ducking de {depth:.1f} dB seulement"


def test_the_voice_never_moves_while_the_bed_ducks(tmp_path, voiced, monkeypatch):
    """`ratio=1` desactive le ducking : c'est la reference « lit a plat ».

    Les deux rendus ne different que par le ducking, donc la bande de la voix
    doit rester identique — c'est la definition de « la voix n'est jamais
    duckee » (spec #12), verifiee sur le signal et non sur le graphe.
    """
    plan, library = _plan_and_library(tmp_path, bed=True)

    ducked = _render_mix(voiced, plan, library, tmp_path / "ducked.wav")
    monkeypatch.setattr(sound_module, "DUCK_RATIO", 1.0)
    flat = _render_mix(voiced, plan, library, tmp_path / "flat.wav")

    voice_ducked = _mean_band_db(ducked, VOICE_BAND, SPEECH_WINDOWS)
    voice_flat = _mean_band_db(flat, VOICE_BAND, SPEECH_WINDOWS)
    assert abs(voice_ducked - voice_flat) < 1.0, (
        f"la voix bouge de {voice_ducked - voice_flat:+.2f} dB selon le ducking"
    )

    # ... pendant que le lit, lui, s'ecarte franchement — et c'est sous la parole
    # qu'on le mesure : dans un creux il n'y a pas de voix, donc rien a
    # compresser, et les deux rendus y sont identiques par construction.
    bed_moved = (
        _mean_band_db(flat, BED_BAND, SPEECH_WINDOWS)
        - _mean_band_db(ducked, BED_BAND, SPEECH_WINDOWS)
    )
    assert bed_moved > 6.0, f"le lit ne s'ecarte que de {bed_moved:.1f} dB"


# --- AC 2 : les SFX tombent au bon endroit, sans clic --------------------------


def test_each_sfx_lands_on_its_own_cue_timecode(tmp_path, muted):
    plan, library = _plan_and_library(tmp_path, bed=False)
    cues = _cues(plan)
    # Les trois types de cues doivent survivre a l'ecartement minimum : sinon
    # ce test ne mesurerait qu'une partie de ce que le plan sait poser.
    assert set(cues) == {"whoosh", "pop", "rise"}, cues

    mixed = _render_mix(muted, plan, library, tmp_path / "sfx.wav")

    for kind, at in cues.items():
        band = SFX_BANDS[kind]
        on_cue = _band_db(mixed, band, at + 0.02, 0.05)
        # 1.5s plus tot ou plus tard, on n'y entend que le silence de la source.
        away = max(
            _band_db(mixed, band, max(0.0, at - 1.5) + 0.02, 0.05),
            _band_db(mixed, band, at + 1.5, 0.05),
        )
        assert on_cue - away > 20.0, (
            f"{kind} a {at:.2f}s ne se distingue pas du fond ({on_cue - away:.1f} dB)"
        )


def test_sfx_edges_are_faded_so_nothing_crack_pops_through(tmp_path, muted):
    """Les SFX du test ont des bords francs : sans fondu, on entendrait un clic.

    On le verifie sur le signal : les premieres et les dernieres millisecondes du
    SFX sont bien plus faibles que son corps, et le mix commence et finit en
    silence — donc aucune discontinuite audible.
    """
    plan, library = _plan_and_library(tmp_path, bed=False)
    pop_at = next(cue.at for cue in plan.cues if cue.kind == "pop")
    pop = library.sfx_for("pop")[0]

    mixed = _render_mix(muted, plan, library, tmp_path / "sfx.wav")
    band = SFX_BANDS["pop"]

    body = _band_db(mixed, band, pop_at + 0.03, 0.03)
    edge_in = _band_db(mixed, band, pop_at, 0.002)
    tail = _band_db(mixed, band, pop_at + pop.duration - 0.004, 0.004)
    assert edge_in < body - 10.0, f"pas de fondu d'entree ({edge_in:.1f} vs {body:.1f})"
    assert tail < body - 10.0, f"pas de fondu de sortie ({tail:.1f} vs {body:.1f})"
    assert SFX_FADE_OUT < pop.duration, "fixture : le fondu de sortie doit tenir dans le SFX"

    # Un clic, c'est aussi un signal qui ne commence ni ne finit par zero.
    assert abs(float(mixed[0])) < 1e-4, "le mix demarre en discontinuite"
    assert abs(float(mixed[-1])) < 1e-4, "le mix finit en discontinuite"


# --- AC 3 : zero appel reseau au rendu -----------------------------------------


def test_resolving_and_mixing_the_bundle_never_touches_the_network(tmp_path, voiced):
    """Le spec #12 exige zero appel reseau au runtime, licence CC0 a l'appui.

    On coupe les sockets pendant la resolution du bundle, la preparation du clip
    et la construction du graphe : si le moindre chemin sortait chercher un
    fichier ou une API, le test echouerait ici.
    """
    def forbidden(*args, **kwargs):
        raise AssertionError("le rendu a tente un appel reseau")

    original = (socket.socket, socket.create_connection)
    socket.socket = forbidden
    socket.create_connection = forbidden
    try:
        plan, library = _plan_and_library(tmp_path, bed=True)
        shipped = load_sound_library()
        prepared = prepare_clip_sound(
            voiced, KEEP_RANGES, duration=PLAN_DURATION, has_hook=True, seed="clip-1",
        )
        mix = build_audio_mix_graph(plan=plan, library=library, seed="clip-1")
    finally:
        socket.socket, socket.create_connection = original

    assert prepared is not None and mix is not None
    # Le bundle livre resout lui aussi sans reseau, licence CC0 comprise.
    assert [asset.id for asset in shipped.all_assets()]

    # Toutes les entrees du rendu sont des fichiers presents sur le disque.
    inputs = [
        mix.input_args[i + 1] for i, value in enumerate(mix.input_args) if value == "-i"
    ]
    assert inputs
    for value in inputs:
        assert Path(value).is_file(), f"entree non locale : {value}"


# --- AC 4 : niveau final au standard des plateformes ---------------------------


def test_the_mix_lands_on_the_platform_loudness_standard(tmp_path, voiced):
    plan, library = _plan_and_library(tmp_path, bed=True)
    out = tmp_path / "mix.wav"
    mixed = _render_mix(voiced, plan, library, out)

    measured = _loudness(out)
    assert abs(measured["i"] - sound_module.LOUDNESS_TARGET_LUFS) <= 0.5, (
        f"{measured['i']:.1f} LUFS, cible {sound_module.LOUDNESS_TARGET_LUFS}"
    )
    assert measured["tp"] <= sound_module.LOUDNESS_TARGET_TP + 0.5, (
        f"true peak {measured['tp']:.1f} dBFS, plafond {sound_module.LOUDNESS_TARGET_TP}"
    )
    # La plage LRA est une cible, pas un plafond : on verifie qu'elle reste
    # autour de celle visee, pas qu'elle est egale au chiffre.
    assert measured["lra"] <= sound_module.LOUDNESS_TARGET_LRA + 4.0, (
        f"LRA {measured['lra']:.1f} LU, bien au-dessus de la cible "
        f"{sound_module.LOUDNESS_TARGET_LRA} LU"
    )
    assert float(np.abs(mixed).max()) < 1.0, "le mix clippe"


# --- la passe finale reellement assemblee --------------------------------------


def test_the_final_render_puts_the_mix_and_the_video_in_one_pass(tmp_path, render_source):
    """Un seul encodage : video habillee + voix + lit + SFX, en 1080x1920."""
    prepared = prepare_clip_sound(
        render_source, RENDER_SOURCE_RANGES, duration=RENDER_SOURCE_DURATION, has_hook=True, seed="clip-1",
    )
    assert prepared is not None

    output = tmp_path / "clip.mp4"
    ok, width, height = render_reframed_clip_ffmpeg(
        render_source, output, "vertical", sound=prepared
    )
    assert ok, "le rendu final a echoue"
    assert (width, height) == (1080, 1920)

    streams = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
            check=True, capture_output=True, text=True,
        ).stdout
    )["streams"]
    assert {stream["codec_type"] for stream in streams} == {"video", "audio"}
    video = next(s for s in streams if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)

    # Le son du fichier livre est bien le mixage normalise, pas la voix nue :
    # dans les creux de la source, le mix contient le lit.
    gaps, speech = windows(RENDER_SOURCE_SECONDS)
    rendered = _decode(output)
    assert _rms_db(rendered, gaps) > -60.0, "le lit n'a pas survecu au rendu final"
    assert _rms_db(rendered, speech) > -30.0, "la voix a disparu"
    assert _loudness(output)["i"] > sound_module.LOUDNESS_TARGET_LUFS - 2.0


def test_without_sound_the_render_keeps_the_voice_alone(tmp_path, render_source):
    """Regression : sans bundle audio, le rendu reste la voix nue — les creux de
    la source y sont muets, et la voix y est bien presente."""
    output = tmp_path / "plain.mp4"
    ok, _, _ = render_reframed_clip_ffmpeg(render_source, output, "vertical")
    assert ok

    gaps, speech = windows(RENDER_SOURCE_SECONDS)
    rendered = _decode(output)
    # Sans bed dans le bundle, le rendu ne doit pas faire reverberer les creux :
    # la source y est muette, donc le doit rester.
    assert _rms_db(rendered, gaps) < -80.0, (
        "un lit musical s'est glisse dans un rendu sans son"
    )
    assert _rms_db(rendered, speech) > -30.0, "la voix a disparu"


def test_a_source_without_audio_stays_without_audio(tmp_path):
    """Un clip muet n'a rien a mixer : le rendu doit rester muet, et surtout ne
    pas creer de piste audio a partir du lit musical."""
    silent = _mux_no_audio(tmp_path)
    prepared = prepare_clip_sound(
        silent, [(0.0, RENDER_SOURCE_SECONDS)], duration=RENDER_SOURCE_SECONDS, seed="silencieux",
    )
    assert prepared is not None, "la preparation du son ne depend pas de la piste audio"

    output = tmp_path / "clip.mp4"
    ok, _, _ = render_reframed_clip_ffmpeg(silent, output, "vertical", sound=prepared)
    assert ok

    streams = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
            check=True, capture_output=True, text=True,
        ).stdout
    )["streams"]
    assert "audio" not in {stream["codec_type"] for stream in streams}


def _mux_no_audio(tmp_path: Path) -> Path:
    out = tmp_path / "sans-audio.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=24:duration={RENDER_SOURCE_SECONDS}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


#: Les tests de rendu complet encodent en 1080x1920 et analysent la source
#: (detection de visage) : c'est la partie la plus lente de la suite. On leur
#: donne une source courte, suffisante pour prouver l'assemblage.
RENDER_SOURCE_SECONDS = 4.0
RENDER_SOURCE_RANGES = [(0.0, 2.0), (2.0, 4.0)]
RENDER_SOURCE_DURATION = 4.0 - 0.22


@pytest.fixture(scope="module")
def render_source(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rendu")
    audio = _write_voice(tmp / "voice.wav", RENDER_SOURCE_SECONDS)
    return _mux(tmp, audio, "source.mp4", RENDER_SOURCE_SECONDS)
