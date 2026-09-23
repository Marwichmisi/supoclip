"""T2 — persistance clip.preset : UPDATE + tolerance DB pre-T1."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.exc import DBAPIError

from src.repositories.clip_repository import ClipRepository


def mock_db():
    db = AsyncMock()
    db.info = {}
    return db


async def test_set_motion_preset_updates_column():
    db = mock_db()
    await ClipRepository.set_motion_preset(db, "clip-1", "calme")
    statement = str(db.execute.call_args[0][0])
    assert "SET preset" in statement
    assert db.execute.call_args[0][1] == {"clip_id": "clip-1", "preset": "calme"}
    db.commit.assert_awaited_once()


async def test_set_motion_preset_ignores_pre_t1_databases():
    db = mock_db()
    db.execute.side_effect = DBAPIError(
        "UPDATE", {}, SimpleNamespace(sqlstate="42703")
    )
    await ClipRepository.set_motion_preset(db, "clip-1", "calme")  # ne leve pas


async def test_set_motion_preset_reraises_other_errors():
    db = mock_db()
    db.execute.side_effect = DBAPIError(
        "UPDATE", {}, SimpleNamespace(sqlstate="23503")
    )
    try:
        await ClipRepository.set_motion_preset(db, "clip-1", "calme")
    except DBAPIError:
        return
    raise AssertionError("aurait du lever")
