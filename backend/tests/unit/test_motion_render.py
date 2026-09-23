"""T2 — Presets motion + progress-bar : comportement externe (pixels, timings).

- Meme source rendue en Calme et Energie visiblement differente.
- Progress-bar presente par defaut, absente apres opt-out.
- Export 1080x1920 avec transitions du preset (fade 0.22s / slide 0.30s).
"""

import json
import shutil
import subprocess

import pytest

from src.editor_document import EditDocument, render_document


def document(**changes):
    return EditDocument.model_validate(
        {
            "segments": [
                {"id": "a", "start": 0.25, "end": 1.0},
                {"id": "b", "start": 1.25, "end": 2.0},
            ],
            **changes,
        }
    )


def make_source(path, audio=False):
    args = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x240:rate=24:duration=2",
    ]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac"]
    subprocess.run(args + [str(path)], check=True, capture_output=True)


def filter_complex_of(monkeypatch, directory, doc, preset="tiktok"):
    from src import editor_document as module

    captured = {}
    monkeypatch.setattr(
        module, "run_render", lambda command, *args: captured.setdefault("cmd", command)
    )
    render_document(directory, "j" * 32, doc, preset)
    command = captured["cmd"]
    return command[command.index("-filter_complex") + 1]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_filtergraph_calme_fade_and_progress(tmp_path, monkeypatch):
    make_source(tmp_path / "clean.mp4")
    graph = filter_complex_of(monkeypatch, tmp_path, document(motion_preset="calme"))
    assert "xfade=transition=fade:duration=0.22" in graph
    assert "acrossfade" not in graph  # pas de piste audio
    assert "zoompan=z='1+(0.12)*on/" in graph  # punch-in doux 1.0 -> 1.12
    assert "drawbox" in graph  # progress-bar par defaut
    assert "slideleft" not in graph


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_filtergraph_energie_whip_no_progress_with_audio(tmp_path, monkeypatch):
    make_source(tmp_path / "clean.mp4", audio=True)
    graph = filter_complex_of(
        monkeypatch, tmp_path, document(motion_preset="energie", progress_bar=False)
    )
    assert "xfade=transition=slideleft:duration=0.3" in graph
    assert "acrossfade=d=0.3" in graph
    assert "zoompan=z='1+(0.25)*on/" in graph  # punch-in marque 1.0 -> 1.25
    assert "drawbox" not in graph  # opt-out editeur
    assert "transition=fade:" not in graph


def frame(path, at):
    out = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(at),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    import numpy as np

    return np.frombuffer(out, dtype="uint8").reshape(1920, 1080, 3).astype(float)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_same_source_renders_visibly_different_and_sized(tmp_path):
    make_source(tmp_path / "clean.mp4")
    renders = {}
    for name, doc in [
        ("calme", document(motion_preset="calme")),
        ("energie", document(motion_preset="energie")),
        ("sans_barre", document(progress_bar=False)),
    ]:
        render_document(tmp_path, name * 8, doc, "tiktok")
        renders[name] = tmp_path / f"export-{name * 8}.mp4"

    for path in renders.values():
        probe = json.loads(
            subprocess.run(
                ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
        video = next(s for s in probe["streams"] if s["codec_type"] == "video")
        assert (video["width"], video["height"]) == (1080, 1920)

    import numpy as np

    # Meme instant, presets differents : punch 1.12 vs 1.25 + fade vs slide.
    diff_motion = np.mean(np.abs(frame(renders["calme"], 1.0) - frame(renders["energie"], 1.0)))
    assert diff_motion > 5.0

    # Progress-bar : seule la bande basse de 8px change a l'opt-out.
    basse = lambda img: img[1900:1920]
    diff_barre = np.mean(
        np.abs(basse(frame(renders["energie"], 1.0)) - basse(frame(renders["sans_barre"], 1.0)))
    )
    assert diff_barre > 2.0
    haut_energie = frame(renders["energie"], 1.0)[:1900]
    haut_sans = frame(renders["sans_barre"], 1.0)[:1900]
    assert np.mean(np.abs(haut_energie - haut_sans)) < 0.5
