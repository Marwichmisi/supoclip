"""T3 — contrat HTTP du picker hook et du titre libre."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from src.api.routes import tasks
from src.database import get_db


@pytest.fixture
async def client(monkeypatch):
    task = {"id": "task-1", "user_id": "owner-1", "status": "completed"}
    clip = {
        "id": "clip-1",
        "task_id": "task-1",
        "hook_title": "Variant A",
        "hook_variants": ["Variant A", "Variant B", "Variant C"],
        "selected_hook_variant": 0,
    }
    service = SimpleNamespace(
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=clip)),
        update_clip_hook=AsyncMock(return_value={**clip, "hook_title": "Variant B", "selected_hook_variant": 1}),
    )
    monkeypatch.setattr(tasks, "TaskService", lambda db: service)
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="owner-1"))

    @asynccontextmanager
    async def transaction(*args):
        yield

    db = SimpleNamespace(close=AsyncMock(), info={})
    app = FastAPI()
    app.include_router(tasks.router)
    app.dependency_overrides[get_db] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, service


async def test_select_hook_variant_rerenders_through_task_service(client):
    http, service = client
    response = await http.patch(
        "/tasks/task-1/clips/clip-1/hook",
        json={"variant_index": 1},
    )

    assert response.status_code == 200
    assert response.json()["clip"]["hook_title"] == "Variant B"
    service.update_clip_hook.assert_awaited_once_with(
        "task-1", "clip-1", variant_index=1, hook_title=None
    )


async def test_manual_hook_title_is_accepted_and_re_rendered(client):
    http, service = client
    response = await http.patch(
        "/tasks/task-1/clips/clip-1/hook",
        json={"hook_title": "Un titre corrige"},
    )

    assert response.status_code == 200
    service.update_clip_hook.assert_awaited_once_with(
        "task-1", "clip-1", variant_index=None, hook_title="Un titre corrige"
    )


async def test_hook_endpoint_rejects_ambiguous_payload(client):
    http, _ = client
    response = await http.patch(
        "/tasks/task-1/clips/clip-1/hook",
        json={"variant_index": 0, "hook_title": "Titre"},
    )
    assert response.status_code == 400
