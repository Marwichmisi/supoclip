"""T3 — export cover toujours en 1080x1920."""

from pathlib import Path
import subprocess

from PIL import Image

from src.cover import (
    COVER_FALLBACK_SEEK_SECONDS,
    COVER_HEIGHT,
    COVER_SEEK_SECONDS,
    COVER_WIDTH,
    render_clip_cover,
)


def test_cover_seeks_after_kinetic_hook_reveal():
    assert COVER_SEEK_SECONDS >= 3.5
    assert COVER_FALLBACK_SEEK_SECONDS < COVER_SEEK_SECONDS


def test_render_clip_cover_outputs_vertical_jpeg(tmp_path):
    source = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x240:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        capture_output=True,
        check=True,
    )

    cover = render_clip_cover(source)

    assert cover == source.with_suffix(".cover.jpg")
    assert cover.exists()
    with Image.open(cover) as image:
        assert image.size == (COVER_WIDTH, COVER_HEIGHT)
        assert image.format == "JPEG"

    first_mtime = cover.stat().st_mtime_ns
    assert render_clip_cover(source) == cover
    assert cover.stat().st_mtime_ns == first_mtime


def test_render_clip_cover_rejects_missing_source(tmp_path):
    try:
        render_clip_cover(tmp_path / "missing.mp4")
    except FileNotFoundError:
        return
    raise AssertionError("expected FileNotFoundError")
