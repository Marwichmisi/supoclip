"""T3 — persistance du choix hook sans casser les bases pré-T1."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.exc import DBAPIError

from src.repositories.clip_repository import ClipRepository


def mock_db():
    db = AsyncMock()
    db.info = {}
    return db


async def test_set_hook_selection_persists_title_and_index():
    db = mock_db()
    await ClipRepository.set_hook_selection(
        db,
        "clip-1",
        "Variante B",
        1,
        ["Variante A", "Variante B", "Variante C"],
    )

    statement = str(db.execute.call_args[0][0])
    assert "SET hook_title" in statement
    assert db.execute.call_args[0][1] == {
        "clip_id": "clip-1",
        "hook_title": "Variante B",
        "hook_variants": '["Variante A", "Variante B", "Variante C"]',
        "selected_hook_variant": 1,
    }
    db.commit.assert_awaited_once()


async def test_set_hook_selection_ignores_pre_t1_databases():
    db = mock_db()
    db.execute.side_effect = DBAPIError(
        "UPDATE", {}, SimpleNamespace(sqlstate="42703")
    )
    await ClipRepository.set_hook_selection(db, "clip-1", "Variante", 0, ["Variante"])


async def test_set_hook_selection_reraises_other_database_errors():
    db = mock_db()
    db.execute.side_effect = DBAPIError(
        "UPDATE", {}, SimpleNamespace(sqlstate="23503")
    )
    try:
        await ClipRepository.set_hook_selection(db, "clip-1", "Variante", 0, ["Variante"])
    except DBAPIError:
        return
    raise AssertionError("expected DBAPIError")
