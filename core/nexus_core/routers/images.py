"""Images for chat messages: POST /images to attach one, GET /images/{id} to show it.

An image is uploaded the moment it is attached, before the message is sent,
and the message then names it by id (MessageCreate.images). The upload also
asks the runtime to load the vision model, so it is ready by Send.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ..auth import current_user_id as _current_user_id
from ..images import ImageError, ImageStore, warm_vision

router = APIRouter(prefix="/images", tags=["images"])


def _store(request: Request) -> ImageStore:
    store = getattr(request.app.state, "images", None)
    if store is None:
        raise HTTPException(status_code=503, detail="image storage is not configured")
    return store


@router.post("", status_code=201)
async def upload_image(request: Request, file: UploadFile = File(...)) -> dict:
    user_id = _current_user_id(request)
    store = _store(request)
    limit = getattr(request.app.state.settings, "image_max_mb", 20) * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"images can be at most {limit // (1024 * 1024)} MB")
    try:
        # Decoding and re-encoding a big photo takes a moment; not on the loop.
        stored = await asyncio.to_thread(store.save, user_id, data, file.filename or "image")
    except ImageError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    tasks = getattr(request.app.state, "background_tasks", None)
    if tasks is None:
        tasks = request.app.state.background_tasks = set()
    task = asyncio.create_task(warm_vision(request.app))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return stored.meta()


@router.get("/{image_id}")
async def get_image(image_id: uuid.UUID, request: Request) -> FileResponse:
    user_id = _current_user_id(request)
    store = _store(request)
    image = store.get(user_id, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(store.file(user_id, image), media_type=image.mime_type,
                        headers={"Cache-Control": "private, max-age=86400"})
