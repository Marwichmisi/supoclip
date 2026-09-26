"""T5 — Voix pro : comportement externe du rendu (spec #12, ticket #20).

On ne teste pas le graphe mais ce qu'un createur entend :
- souffle phone/street reduit (hiss + rumble) sans manger la voix ;
- niveau final au standard plateformes ;
- chaine transcription strictement inchangee.

Sources synthetiques :
- `noisy` : voix 180 Hz + harmoniques gatee (1.2s parole / 0.8s silence),
  souffle blanc continu, rumble 50 Hz, sibilance 7 kHz sur la parole.
  Le gate donne des silences vrais pour mesurer le souffle seul.
"""

import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from src.media.ffmpeg import build_audio_output_args
from src.media.voice import build_voice_filter

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg required",
)

RATE = 48000
SECONDS = 6.0
GAPS = [(1.35, 0.5), (3.35, 0.5), (5.35, 0.5)]
SPEECH = [(0.35, 0.5), (2.35, 0.5), (4.35, 0.5)]


def _write_noisy(path: Path) -> Path:
    t = np.arange(int(RATE * SECONDS)) / RATE
    gate = (t % 2.0 < 1.2).astype(float)
    voice = (
        0.5 * np.sin(2 * np.pi * 180 * t)
        + 0.25 * np.sin(2 * np.pi * 360 * t)
        + 0.15 * np.sin(2 * np.pi * 2500 * t)
    ) * gate
    rng = np.random.default_rng(0)
    hiss = 0.02 * rng.standard_normal(len(t))
    rumble = 0.08 * np.sin(2 * np.pi * 50 * t)
    sibil = 0.05 * np.sin(2 * np.pi * 7000 * t) * gate
    mix = np.clip(voice + hiss + rumble + sibil, -1, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes((mix * 32767).astype(np.int16).tobytes())
    return path


def _mux(tmp_path: Path, audio: Path, name: str) -> Path:
    out = tmp_path / name
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i",
            f"testsrc2=size=320x240:rate=24:duration={SECONDS}",
            "-i", str(audio),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", str(RATE), "-ac", "1",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


def _run_af(source: Path, filt: str, out: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(source),
         "-af", filt, "-c:a", "pcm_s16le", str(out)],
        check=True,
        capture_output=True,
    )
    return out


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
    low, high = band
    segment = samples[int(at * RATE):int((at + duration) * RATE)]
    assert len(segment) >= 64, f"fenetre vide a {at}s"
    window = np.hanning(len(segment))
    spectrum = np.abs(np.fft.rfft(segment * window))
    freqs = np.fft.rfftfreq(len(segment), 1 / RATE)
    in_band = spectrum[(freqs >= low) & (freqs < high)]
    rms = float(
        np.sqrt((in_band ** 2).sum()) / len(segment) / np.sqrt((window ** 2).mean())
    )
    return 20 * np.log10(rms + 1e-12)


def _mean_band(samples: np.ndarray, band, windows) -> float:
    return float(np.mean([_band_db(samples, band, at, dur) for at, dur in windows]))


def _loudness(path: Path) -> dict:
    import re

    report = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(path),
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stderr
    summary = report[report.rfind("Summary:"):]
    integrated = re.search(r"I:\s+(-?\d+\.?\d*) LUFS", summary)
    peak = re.search(r"Peak:\s+(-?\d+\.?\d*) dBFS", summary)
    assert integrated and peak, f"mesure ebur128 inexploitable : {summary[-300:]}"
    return {"i": float(integrated.group(1)), "tp": float(peak.group(1))}


@pytest.fixture(scope="module")
def noisy_clip(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("voix")
    wav = _write_noisy(tmp / "noisy.wav")
    return _mux(tmp, wav, "noisy.mp4")


def test_hiss_and_rumble_drop_while_voice_stays(tmp_path, noisy_clip):
    """Souffle reduit sans artefacts : hiss -3 dB, rumble -2 dB, voix a ±2 dB."""
    filt = build_voice_filter()
    treated = _decode(_run_af(noisy_clip, filt, tmp_path / "voix.wav"))
    raw = _decode(noisy_clip)

    hiss_drop = _mean_band(raw, (6000, 8000), GAPS) - _mean_band(
        treated, (6000, 8000), GAPS
    )
    assert hiss_drop >= 3.0, f"hiss reduit de {hiss_drop:.1f} dB seulement"

    rumble_drop = _mean_band(raw, (30, 90), GAPS) - _mean_band(
        treated, (30, 90), GAPS
    )
    assert rumble_drop >= 2.0, f"rumble reduit de {rumble_drop:.1f} dB seulement"

    voice_delta = _mean_band(treated, (150, 500), SPEECH) - _mean_band(
        raw, (150, 500), SPEECH
    )
    assert abs(voice_delta) <= 2.0, f"voix bouge de {voice_delta:+.1f} dB"
    assert float(np.abs(treated).max()) < 1.0, "la chaine voix clippe"


def test_final_level_meets_platform_standard(tmp_path, noisy_clip):
    """Niveau final conforme : -14 LUFS, true peak sous le plafond."""
    args = build_audio_output_args(True)
    assert "-af" in args
    filt = args[args.index("-af") + 1]
    assert "loudnorm" in filt
    # Normalisation en dernier, comme l'exige le spec.
    assert filt.rstrip().endswith("LRA=11") or "loudnorm" in filt.split(",")[-1]

    out = tmp_path / "norm.wav"
    _run_af(noisy_clip, filt, out)
    measured = _loudness(out)
    assert abs(measured["i"] - (-14)) <= 1.0, f"{measured['i']:.1f} LUFS"
    assert measured["tp"] <= -1.0, f"true peak {measured['tp']:.1f} dBFS"


def test_transcription_chain_is_untouched():
    """Garde anti-degradation : la transcription lit l'audio brut, jamais filtre."""
    from src.media import transcription as tr

    source = Path(tr.__file__).read_text(encoding="utf-8")
    assert "build_voice_filter" not in source
    assert "afftdn" not in source
    assert "deesser" not in source
    assert "acompressor" not in source
    # Le filtre voix existe et porte les six maillons, dans l'ordre du spec.
    filt = build_voice_filter()
    stages = [fragment.split("=")[0] for fragment in filt.split(",")]
    assert stages == [
        "highpass", "afftdn", "deesser", "equalizer", "acompressor", "alimiter",
    ]
