"""Images attached to chat messages: where they are kept, and their captions.

An image is uploaded on its own (POST /images) while the person is still
typing, and the message that follows names it by id. Uploading early is what
lets the vision model load during the typing rather than after Send.

Each person's images live in their own folder under ``CORE_IMAGE_DIR``, as
``<id>.jpg`` (or ``.png`` when it has transparency) beside ``<id>.json``,
which holds its original name and size. Every image is re-encoded on the way
in: the copy kept is only pixels, so a phone photo's location and camera data
never reach the disk. A path is only ever built from the signed-in person's
id and a UUID, so nobody can read another person's images.

The vision model answers the turn with the image itself. Afterwards it writes
a one-line caption onto the message (``meta.images[i].caption``), and that is
how later turns, typed or spoken and answered by the chat model, know what
the image showed.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


logger = logging.getLogger(__name__)

# Kept no larger than this; the runtime shrinks again (to 1280) for the model.
STORE_MAX_SIDE = 2048

CAPTION_PROMPT = (
    "Describe this image in one or two plain sentences, for someone who can't "
    "see it. Include any text in it that matters, word for word, and amounts "
    "exactly as printed. Only what is visible: no comments, no guesses about "
    "where or when, no currency that isn't printed."
)


class ImageError(ValueError):
    """An upload that isn't an image Nexus can read."""


@dataclass(frozen=True)
class StoredImage:
    id: uuid.UUID
    name: str
    width: int
    height: int
    mime_type: str

    def meta(self) -> dict:
        return {"id": str(self.id), "name": self.name, "width": self.width,
                "height": self.height, "mime_type": self.mime_type}


class ImageStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _dir(self, user_id: uuid.UUID) -> Path:
        return self.root / str(uuid.UUID(str(user_id)))

    def _paths(self, user_id: uuid.UUID, image_id: uuid.UUID) -> tuple[Path, Path, Path]:
        base = self._dir(user_id) / str(uuid.UUID(str(image_id)))
        return base.with_suffix(".json"), base.with_suffix(".jpg"), base.with_suffix(".png")

    def save(self, user_id: uuid.UUID, data: bytes, name: str) -> StoredImage:
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except (UnidentifiedImageError, OSError) as exc:
            if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc"):
                raise ImageError("HEIC photos aren't supported yet. Export it as JPEG and attach that.") from exc
            raise ImageError("That file isn't an image Nexus can read (JPEG, PNG, WebP or GIF).") from exc
        # Upright as the camera meant it, then pixels only: what is written
        # carries no EXIF, so no GPS position or device details.
        image = ImageOps.exif_transpose(image)
        image.thumbnail((STORE_MAX_SIDE, STORE_MAX_SIDE))
        alpha = image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)
        image = image.convert("RGBA" if alpha else "RGB")

        image_id = uuid.uuid4()
        meta_path, jpg, png = self._paths(user_id, image_id)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        if alpha:
            image.save(png, format="PNG", optimize=True)
            mime = "image/png"
        else:
            image.save(jpg, format="JPEG", quality=90)
            mime = "image/jpeg"
        stored = StoredImage(image_id, (name or "image")[:200], image.width, image.height, mime)
        meta_path.write_text(json.dumps(stored.meta()))
        return stored

    def get(self, user_id: uuid.UUID, image_id: uuid.UUID) -> StoredImage | None:
        meta_path, _, _ = self._paths(user_id, image_id)
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            return None
        return StoredImage(uuid.UUID(meta["id"]), meta["name"], meta["width"], meta["height"], meta["mime_type"])

    def file(self, user_id: uuid.UUID, image: StoredImage) -> Path:
        _, jpg, png = self._paths(user_id, image.id)
        return png if image.mime_type == "image/png" else jpg

    def data_url(self, user_id: uuid.UUID, image: StoredImage) -> str:
        encoded = base64.b64encode(self.file(user_id, image).read_bytes()).decode()
        return f"data:{image.mime_type};base64,{encoded}"

    def delete(self, user_id: uuid.UUID, image_ids) -> int:
        removed = 0
        for image_id in image_ids:
            try:
                for path in self._paths(user_id, uuid.UUID(str(image_id))):
                    if path.exists():
                        path.unlink()
                        removed += 1
            except (OSError, ValueError):
                logger.warning("could not delete image %s", image_id)
        return removed


def image_ids(messages) -> list[str]:
    """Every image id attached to these messages."""
    return [img["id"] for m in messages for img in (m.meta or {}).get("images", []) if img.get("id")]


async def delete_chat_images(app, session, user_id: uuid.UUID, chat_ids: list) -> int:
    """Delete the image files attached to these chats' messages."""
    from sqlalchemy import select

    from .models import Message

    store: ImageStore | None = getattr(app.state, "images", None)
    if store is None or not chat_ids:
        return 0
    rows = (await session.execute(select(Message).where(Message.chat_id.in_(chat_ids)))).scalars().all()
    return store.delete(user_id, image_ids(rows))


def caption_note(meta: dict) -> str:
    """How an earlier image appears in the transcript the chat model reads."""
    notes = []
    for img in meta.get("images", []):
        described = img.get("caption") or "no description yet"
        notes.append(f"[Image attached: {img.get('name', 'image')}. {described}]")
    return "\n".join(notes)


async def caption(provider, model: str, data_url: str, at=None) -> str:
    """One image's caption from the vision model, on one line."""
    text = await provider.chat(
        [{"role": "user", "content": [
            # No date note here: told the date, the model added "which is
            # today, Monday..." to a receipt's caption. Dates in captions are
            # counted by core (clock.calendar_note) instead.
            {"type": "text", "text": CAPTION_PROMPT},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
        model=model, max_tokens=120, priority="interactive", temperature=0,
    )
    return " ".join(text.split())[:600]


async def write_captions(app, user_id: uuid.UUID, message_id: uuid.UUID) -> None:
    """Caption each image on a stored message, in the background, after its
    answer. Never raises: without a caption, later turns see the file name."""
    from .models import Message

    settings = app.state.settings
    store: ImageStore = app.state.images
    try:
        async with app.state.session_factory() as session:
            message = await session.get(Message, message_id)
            if message is None:
                return
            images = [dict(i) for i in (message.meta or {}).get("images", [])]
            changed = False
            for img in images:
                if img.get("caption"):
                    continue
                stored = store.get(user_id, uuid.UUID(img["id"]))
                if stored is None:
                    continue
                img["caption"] = await caption(app.state.provider, settings.vision_model,
                                               store.data_url(user_id, stored), message.created_at)
                changed = True
            if changed:
                # A new dict: SQLAlchemy only notices a JSON column reassigned.
                message.meta = {**message.meta, "images": images}
                await session.commit()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("captioning images failed for message=%s", message_id)


def schedule_captions(app, user_id: uuid.UUID, message) -> None:
    if not (message.meta or {}).get("images"):
        return
    tasks = getattr(app.state, "background_tasks", None)
    if tasks is None:
        tasks = app.state.background_tasks = set()
    task = asyncio.create_task(write_captions(app, user_id, message.id))
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def warm_vision(app) -> None:
    """Ask the runtime to load the vision model now, so it is ready by the
    time the message is sent. Fire and forget: the answer loads it anyway."""
    from .decisions import runtime_root

    root = runtime_root(getattr(app.state.settings, "openai_base_url", None))
    if root is None:
        return
    try:
        await app.state.http.post(f"{root}/models/vision/load", timeout=120)
    except Exception as exc:
        logger.info("could not warm the vision model: %s", exc)
