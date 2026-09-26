"""T6 — B-roll local : comportement externe du rendu (spec #12, ticket #17).

- mot-cle qui matche => B-roll local incrustre, export 1080x1920 ;
- aucun match => blur cinematique (clip inchange), jamais de trou visuel ;
- zero appel reseau pendant le rendu.
"""

import hashlib
import json
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from src.media.broll_local import (
    BrollLibrary,
    apply_local_broll,
    build_broll_plan,
    load_broll_library,
)

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg required",
)


def _make_clip(path: Path, pattern: str, duration: float = 4.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    if pattern.startswith("color="):
        lavfi = f"{pattern}:s=1080x1920:r=24:d={duration}"
    else:
        # Sources lavfi type testsrc2 : les options suivent le nom par "=".
        lavfi = f"{pattern}=size=1080x1920:rate=24:duration={duration}"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", lavfi,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _write_library(root: Path, keyword: str = "rue"):
    assets_dir = root / "assets" / "broll"
    assets_dir.mkdir(parents=True, exist_ok=True)
    blob = assets_dir / "pixabay-rue.mp4"
    _make_clip(blob, "testsrc2", duration=3.0)
    digest = hashlib.sha256(blob.read_bytes()).hexdigest()
    manifest = assets_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "broll",
                "assets": [
                    {
                        "id": "pixabay-rue-1",
                        "path": "broll/pixabay-rue.mp4",
                        "duration": 3.0,
                        "license": "CC0 1.0 Universal",
                        "source_url": "https://pixabay.com/videos/123",
                        "published_at": "2018-05-01",
                        "sha256": digest,
                        "keywords": [keyword, "ville"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return load_broll_library(manifest)


def _video_size(path: Path):
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    width, height = out.split("x", 1)
    return int(width), int(height)


def _frame_mean(path: Path, at: float):
    out = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    import numpy as np

    return np.frombuffer(out, dtype="uint8").astype(float).mean()


def test_matching_keyword_overlays_local_broll_sized_1080x1920(tmp_path):
    library = _write_library(tmp_path)
    main = _make_clip(tmp_path / "main.mp4", "color=c=red", duration=4.0)
    plan = build_broll_plan(
        [{"keyword": "rue", "timestamp": 1.0, "duration": 2.0}],
        library,
        clip_duration=4.0,
    )
    assert len(plan.cues) == 1

    output = tmp_path / "out.mp4"
    assert apply_local_broll(main, plan, library, output) is True
    assert output.is_file()
    assert _video_size(output) == (1080, 1920)
    # Le B-roll (mire) remplace le fond rouge : les pixels bougent vraiment.
    before = _frame_mean(main, 1.5)
    after = _frame_mean(output, 1.5)
    assert abs(after - before) > 1.0


def test_no_match_keeps_blur_cinematic_without_hole(tmp_path):
    library = _write_library(tmp_path)
    main = _make_clip(tmp_path / "main.mp4", "color=c=red", duration=4.0)
    plan = build_broll_plan(
        [{"keyword": "ocean", "timestamp": 1.0, "duration": 2.0}],
        library,
        clip_duration=4.0,
    )
    assert plan.cues == []

    output = tmp_path / "out.mp4"
    assert apply_local_broll(main, plan, library, output) is False
    assert not output.exists()
    # Pas de trou visuel : le clip d'origine reste un export valide 1080x1920.
    assert _video_size(main) == (1080, 1920)


def test_render_makes_no_network_call(tmp_path, monkeypatch):
    library = _write_library(tmp_path)
    main = _make_clip(tmp_path / "main.mp4", "color=c=red", duration=4.0)
    plan = build_broll_plan(
        [{"keyword": "rue", "timestamp": 0.5, "duration": 2.0}],
        library,
        clip_duration=4.0,
    )

    def _blocked(*args, **kwargs):
        raise AssertionError("appel reseau interdit pendant le rendu T6")

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "socket", _blocked)

    output = tmp_path / "out.mp4"
    assert apply_local_broll(main, plan, library, output) is True
    assert _video_size(output) == (1080, 1920)


def test_broll_module_has_no_network_dependency():
    import ast

    import src.media.broll_local as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    assert "httpx" not in imported
    assert "requests" not in imported
    assert "socket" not in imported
    assert "urllib" not in imported


def test_empty_shipped_bundle_degrades_to_blur():
    library = BrollLibrary()
    plan = build_broll_plan(
        [{"keyword": "rue", "timestamp": 1.0}],
        library,
        clip_duration=4.0,
    )
    assert plan.cues == []
