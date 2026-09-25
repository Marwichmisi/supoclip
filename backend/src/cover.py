"""Deterministic 9:16 cover rendering for generated clips."""

from __future__ import annotations

from pathlib import Path
import os
import tempfile

from .media.ffmpeg import run_ffmpeg_command

COVER_WIDTH = 1080
COVER_HEIGHT = 1920
COVER_SEEK_SECONDS = 3.8
COVER_FALLBACK_SEEK_SECONDS = 0.5


def cover_path_for_clip(clip_path: Path) -> Path:
    """Return the stable sidecar path used for a clip cover."""
    return clip_path.with_suffix(".cover.jpg")


def render_clip_cover(clip_path: Path, output_path: Path | None = None) -> Path:
    """Extract a downloadable 1080x1920 JPEG after the hook appears.

    The generated clip already contains the selected burned-in hook, so the
    cover is a frame of that rendered result rather than a second independently
    styled render.  The primary seek lands after the kinetic title reveal; a
    shorter clip falls back to an early frame.  A temporary file keeps failed
    ffmpeg runs from exposing a partial image.
    """
    if not clip_path.exists():
        raise FileNotFoundError(str(clip_path))
    destination = output_path or cover_path_for_clip(clip_path)
    if (
        destination.exists()
        and destination.stat().st_mtime >= clip_path.stat().st_mtime
    ):
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)

    def extract(seek_seconds: float) -> bool:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.stem}-",
            suffix=".jpg",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        try:
            result = run_ffmpeg_command(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(seek_seconds),
                    "-i",
                    str(clip_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale={COVER_WIDTH}:{COVER_HEIGHT}:force_original_aspect_ratio=increase,"
                    f"crop={COVER_WIDTH}:{COVER_HEIGHT}",
                    "-q:v",
                    "2",
                    str(temporary_path),
                ],
                timeout=120,
            )
            if (
                result.returncode == 0
                and temporary_path.exists()
                and temporary_path.stat().st_size > 0
            ):
                os.replace(temporary_path, destination)
                return True
            return False
        finally:
            temporary_path.unlink(missing_ok=True)

    if extract(COVER_SEEK_SECONDS) or extract(COVER_FALLBACK_SEEK_SECONDS):
        return destination
    raise RuntimeError("ffmpeg cover render failed")
