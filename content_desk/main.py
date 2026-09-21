"""FastAPI server and static operator UI for the content desk."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from content_desk.config import ContentDeskSettings, load_settings
from content_desk.drama_client import DramaAPIError, DramaClient
from content_desk.flow import (
    approve_board,
    approve_plates,
    approve_script,
    enrol_boards,
    enrol_cast,
    enrol_video,
    estimate_batch,
    measure_board_exposure,
    new_idempotency_prefix,
    resolve_preset,
    start_draft,
)
from content_desk.store import DeskRecord, DeskStore

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


class CreateDeskRequest(BaseModel):
    """Start one series desk."""

    title: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    band: str = Field(default="15s", pattern=r"^(15s|30s|60s)$")
    preset_id: str | None = None
    video_lane: str = Field(default="minimax-h3")


class BoardApproveRequest(BaseModel):
    """Approve boards after exposure."""

    accept_dim: bool = False


class VideoEnrolRequest(BaseModel):
    """Enrol one take with optional caption preset."""

    caption_style: str = "house"


def desk_to_json(desk: DeskRecord) -> dict[str, Any]:
    """Serialize one desk for the UI."""

    payload = asdict(desk)
    payload["gates"] = {key: asdict(gate) for key, gate in desk.gates.items()}
    return payload


def _client(settings: ContentDeskSettings, desk: DeskRecord) -> DramaClient:
    return DramaClient(
        base_url=settings.drama_api_base_url,
        token=settings.require_token(),
        session_id=desk.session_id,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load settings once at startup."""

    app.state.settings = load_settings()
    app.state.store = DeskStore(app.state.settings.resolved_data_dir())
    yield


app = FastAPI(title="Fictora Content Creation", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness probe."""

    return {"status": "ok"}


@app.get("/api/config")
def public_config() -> dict[str, str]:
    """Return non-secret configuration for the UI."""

    settings: ContentDeskSettings = app.state.settings
    return {
        "drama_api_base_url": settings.drama_api_base_url,
        "data_dir": str(settings.resolved_data_dir()),
        "token_configured": str(bool(settings.drama_service_token.strip())),
    }


@app.get("/api/desks")
def list_desks() -> list[dict[str, Any]]:
    """List desks newest first."""

    store: DeskStore = app.state.store
    return [desk_to_json(desk) for desk in store.list()]


@app.post("/api/desks")
def create_desk(body: CreateDeskRequest) -> dict[str, Any]:
    """Create a desk, start a draft, and return the updated record."""

    settings: ContentDeskSettings = app.state.settings
    store: DeskStore = app.state.store
    client = DramaClient(base_url=settings.drama_api_base_url, token=settings.require_token())
    try:
        preset_id, preset_version = resolve_preset(client, body.preset_id)
        desk = store.create(
            title=body.title,
            prompt=body.prompt,
            band=body.band,
            session_id=client.session_id,
            preset_id=preset_id,
            preset_version=preset_version,
            video_lane=body.video_lane,
        )
        desk = start_draft(client, store, desk, idempotency_prefix=new_idempotency_prefix(desk))
    except DramaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except (RuntimeError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        client.close()
    return desk_to_json(desk)


def _load_desk(desk_id: str) -> tuple[DeskStore, DeskRecord, ContentDeskSettings]:
    store: DeskStore = app.state.store
    settings: ContentDeskSettings = app.state.settings
    try:
        desk = store.get(desk_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="desk not found") from exc
    return store, desk, settings


@app.get("/api/desks/{desk_id}")
def get_desk(desk_id: str) -> dict[str, Any]:
    """Fetch one desk."""

    _, desk, _ = _load_desk(desk_id)
    return desk_to_json(desk)


def _run_action(desk_id: str, action: str, runner) -> dict[str, Any]:
    store, desk, settings = _load_desk(desk_id)
    client = _client(settings, desk)
    prefix = new_idempotency_prefix(desk)
    try:
        desk = runner(client, store, desk, prefix)
    except DramaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except (RuntimeError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        client.close()
    return desk_to_json(desk)


@app.post("/api/desks/{desk_id}/cast/enrol")
def api_cast_enrol(desk_id: str) -> dict[str, Any]:
    """Draw cast plates (paid). Stop before approve."""

    return _run_action(desk_id, "cast_enrol", lambda c, s, d, p: enrol_cast(c, s, d, prefix=p))


@app.post("/api/desks/{desk_id}/gates/plates/approve")
def api_plates_approve(desk_id: str) -> dict[str, Any]:
    """Human yes on cast plates."""

    return _run_action(desk_id, "plates_approve", lambda c, s, d, p: approve_plates(c, s, d, prefix=p))


@app.post("/api/desks/{desk_id}/gates/script/approve")
def api_script_approve(desk_id: str) -> dict[str, Any]:
    """Human yes on script lines."""

    return _run_action(desk_id, "script_approve", lambda c, s, d, p: approve_script(c, s, d, prefix=p))


@app.post("/api/desks/{desk_id}/boards/enrol")
def api_boards_enrol(desk_id: str) -> dict[str, Any]:
    """Draw boards (paid)."""

    return _run_action(desk_id, "boards_enrol", lambda c, s, d, p: enrol_boards(c, s, d, prefix=p))


@app.get("/api/desks/{desk_id}/boards/exposure")
def api_board_exposure(desk_id: str) -> dict[str, Any]:
    """Measure board luma via product API."""

    store, desk, settings = _load_desk(desk_id)
    client = _client(settings, desk)
    try:
        desk = measure_board_exposure(client, store, desk)
    except DramaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    finally:
        client.close()
    return desk_to_json(desk)


@app.post("/api/desks/{desk_id}/gates/board/approve")
def api_board_approve(desk_id: str, body: BoardApproveRequest) -> dict[str, Any]:
    """Human yes on boards."""

    return _run_action(
        desk_id,
        "board_approve",
        lambda c, s, d, p: approve_board(c, s, d, prefix=p, accept_dim=body.accept_dim),
    )


@app.post("/api/desks/{desk_id}/estimate")
def api_estimate(desk_id: str) -> dict[str, Any]:
    """Price the batch before video."""

    store, desk, settings = _load_desk(desk_id)
    client = _client(settings, desk)
    try:
        desk = estimate_batch(client, store, desk)
    except DramaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    finally:
        client.close()
    return desk_to_json(desk)


@app.post("/api/desks/{desk_id}/video/enrol")
def api_video_enrol(desk_id: str, body: VideoEnrolRequest) -> dict[str, Any]:
    """Film one take and fetch delivery (paid)."""

    return _run_action(
        desk_id,
        "video_enrol",
        lambda c, s, d, p: enrol_video(c, s, d, prefix=p, caption_style=body.caption_style),
    )


@app.get("/")
def index() -> FileResponse:
    """Serve the operator UI."""

    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def run() -> None:
    """CLI entry: ``content-desk``."""

    settings = load_settings()
    uvicorn.run(
        "content_desk.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
