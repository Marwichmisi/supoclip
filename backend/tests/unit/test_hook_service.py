"""T3 — re-render hook depuis les segments source, sans re-transcription."""

from unittest.mock import AsyncMock

import pytest

from src.config import Config
from src.services import clip_service as clip_service_module
from src.services.task_service import TaskService


@pytest.mark.asyncio
async def test_selecting_variant_rebuilds_source_clip_with_new_title(
    isolated_clip_edits, monkeypatch, tmp_path
):
    config = Config()
    config.temp_dir = str(tmp_path)
    db = AsyncMock()
    db.info = {}
    service = TaskService(db=db, config=config)
    source_path = tmp_path / "source.mp4"
    old_clip_path = tmp_path / "old.mp4"
    source_path.write_bytes(b"source")
    old_clip_path.write_bytes(b"old")
    from src.clip_source_map import save_clip_source_ranges

    save_clip_source_ranges(old_clip_path, [(12.0, 18.0)])
    clip = {
        "id": "clip-1",
        "task_id": "task-1",
        "file_path": str(old_clip_path),
        "start_time": "00:12",
        "end_time": "00:18",
        "duration": 6.0,
        "text": "Une explication assez courte pour devenir un clip",
        "hook_type": "statement",
        "hook_title": "Première variante",
        "hook_variants": ["Première variante", "Deuxième variante", "Troisième variante"],
        "selected_hook_variant": 0,
    }
    service.clip_repo.get_clip_by_id = AsyncMock(
        side_effect=[clip, {**clip, "hook_title": "Deuxième variante", "selected_hook_variant": 1}]
    )
    service.clip_repo.update_clip = AsyncMock()
    service.clip_repo.set_hook_selection = AsyncMock()
    service.task_repo.get_task_by_id = AsyncMock(
        return_value={
            "id": "task-1",
            "source_url": "upload://source.mp4",
            "source_type": "upload",
            "processing_mode": "quality",
            "font_family": "Inter",
            "font_size": 48,
            "font_color": "#123456",
            "caption_template": "minimal",
        }
    )
    service.cache_repo.get_cache = AsyncMock(return_value={"video_path": str(source_path)})
    service._load_task_source_settings = AsyncMock(return_value={"output_format": "vertical"})
    service.video_service.generate_transcript = AsyncMock(
        side_effect=AssertionError("hook re-render must not transcribe")
    )
    calls = []

    def fake_render(*args, **kwargs):
        calls.append((args, kwargs))
        args[3].write_bytes(b"rendered")
        return True

    monkeypatch.setattr(clip_service_module, "create_optimized_clip", fake_render)

    result = await service.update_clip_hook("task-1", "clip-1", variant_index=1)

    assert result["hook_title"] == "Deuxième variante"
    assert calls[0][0][0] == source_path
    assert calls[0][1]["hook_title"] == "Deuxième variante"
    assert calls[0][1]["add_subtitles"] is True
    assert calls[0][1]["keep_ranges"] == [(12.0, 18.0)]
    service.clip_repo.set_hook_selection.assert_awaited_once_with(
        db,
        "clip-1",
        "Deuxième variante",
        1,
        ["Première variante", "Deuxième variante", "Troisième variante"],
    )
    service.video_service.generate_transcript.assert_not_called()
