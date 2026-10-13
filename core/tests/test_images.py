"""Images in chat: kept per person without their metadata, answered by the
vision model, remembered by caption, and deleted with their chat."""

from __future__ import annotations

import asyncio
import io
import uuid

import pytest
from PIL import Image

from nexus_core.images import ImageStore
from nexus_core.main import app

# The seeded demo user (db/seed.sql). Not imported from conftest: run from
# the repository root, "conftest" is the scenario suite's tests/conftest.py.
DEMO_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
VISION = "fake-vision-model"


@pytest.fixture
def provider(provider):
    """conftest's fake provider, also recording which model each call asked for."""
    provider.models = []
    chat = provider.chat

    async def recording(messages, *, model=None, **kwargs):
        provider.models.append(model)
        return await chat(messages, model=model, **kwargs)

    provider.chat = recording
    return provider


@pytest.fixture
async def store(tmp_path, db_client):
    app.state.images = ImageStore(tmp_path / "images")
    app.state.settings.vision_model = VISION
    yield app.state.images
    # Captions are written in the background; in production on a pooled
    # connection of their own, here on the test's one connection.
    await _captions_written()


def _jpeg_with_location() -> bytes:
    image = Image.new("RGB", (3000, 1500), (10, 120, 200))
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (51.0, 30.0, 0.0)}  # GPSInfo
    exif[0x010F] = "PhoneMaker"                     # Make
    buf = io.BytesIO()
    image.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


async def _upload(db_client, data: bytes, name="photo.jpg"):
    return await db_client.post("/images", files={"file": (name, data, "image/jpeg")})


async def _chat(db_client) -> str:
    return (await db_client.post("/chats", json={"title": "Pictures"})).json()["id"]


async def _captions_written() -> None:
    await asyncio.gather(*list(getattr(app.state, "background_tasks", ())))


# ── Storing them ─────────────────────────────────────────────────────────────


async def test_an_upload_is_kept_without_its_location_and_shrunk(db_client, store):
    response = await _upload(db_client, _jpeg_with_location())

    assert response.status_code == 201, response.text
    meta = response.json()
    assert (meta["width"], meta["height"]) == (2048, 1024)
    path = store.file(DEMO_USER_ID, store.get(DEMO_USER_ID, uuid.UUID(meta["id"])))
    kept = Image.open(path)
    assert not kept.getexif()  # no GPS, no camera
    served = await db_client.get(f"/images/{meta['id']}")
    assert served.status_code == 200 and served.headers["content-type"] == "image/jpeg"


async def test_what_isn_t_an_image_is_refused(db_client, store):
    assert (await _upload(db_client, b"%PDF-1.7 not a picture", "doc.pdf")).status_code == 415
    heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64
    response = await _upload(db_client, heic, "IMG_0001.HEIC")
    assert response.status_code == 415 and "HEIC" in response.json()["detail"]


async def test_someone_else_s_image_can_t_be_read_or_attached(db_client, store):
    theirs = store.save(uuid.uuid4(), _jpeg_with_location(), "theirs.jpg")

    assert (await db_client.get(f"/images/{theirs.id}")).status_code == 404
    chat = await _chat(db_client)
    response = await db_client.post(f"/chats/{chat}/messages",
                                    json={"content": "what is this?", "images": [str(theirs.id)]})
    assert response.status_code == 422


# ── Answering with them ──────────────────────────────────────────────────────


async def test_a_message_with_an_image_is_answered_by_the_vision_model(db_client, store, provider):
    image = (await _upload(db_client, _jpeg_with_location())).json()
    chat = await _chat(db_client)

    response = await db_client.post(f"/chats/{chat}/messages",
                                    json={"content": "What colour is this?", "images": [image["id"]]})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["route"]["handler"] == "vision"
    assert body["user_message"]["metadata"]["images"][0]["id"] == image["id"]
    assert provider.models[0] == VISION
    last = provider.chat_calls[0][-1]["content"]
    assert last[0]["type"] == "text" and last[0]["text"].endswith("What colour is this?")
    assert last[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_a_question_about_when_reads_the_image_first(db_client, store, provider):
    """The vision model miscounts days, so the image's text (its caption) is
    read before the answer and its dates counted by core."""
    image = (await _upload(db_client, _jpeg_with_location())).json()
    chat = await _chat(db_client)
    provider.caption = "A notice: final report due 21 November 2026."
    chat_fn = provider.chat

    async def captioning(messages, **kwargs):
        if "Describe this image" in str(messages[0]["content"]):
            provider.models.append(kwargs.get("model"))
            return provider.caption
        return await chat_fn(messages, **kwargs)

    provider.chat = captioning
    body = (await db_client.post(f"/chats/{chat}/messages", json={
        "content": "How long until this is due?", "images": [image["id"]]})).json()

    assert body["user_message"]["metadata"]["images"][0]["caption"] == provider.caption
    answer_prompt = provider.chat_calls[0][-1]["content"][0]["text"]
    assert "Saturday 21 November 2026 is" in answer_prompt
    assert provider.models[:2] == [VISION, VISION]  # read, then answered


async def test_an_image_on_its_own_is_asked_about(db_client, store, provider):
    image = (await _upload(db_client, _jpeg_with_location())).json()
    chat = await _chat(db_client)

    response = await db_client.post(f"/chats/{chat}/messages", json={"images": [image["id"]]})

    assert response.status_code == 201, response.text
    assert provider.chat_calls[0][-1]["content"][0]["text"].endswith("What's in this image?")
    await _captions_written()
    assert (await db_client.post(f"/chats/{chat}/messages", json={"content": "  "})).status_code == 422


async def test_the_next_question_sees_the_image_then_its_caption(db_client, store, provider):
    image = (await _upload(db_client, _jpeg_with_location(), "sign.jpg")).json()
    chat = await _chat(db_client)
    await db_client.post(f"/chats/{chat}/messages", json={"content": "What is this?", "images": [image["id"]]})
    await _captions_written()

    follow_up = (await db_client.post(f"/chats/{chat}/messages", json={"content": "What does it say?"})).json()
    later = (await db_client.post(f"/chats/{chat}/messages", json={"content": "Thanks!"})).json()

    assert follow_up["route"]["handler"] == "vision"
    assert later["route"]["handler"] == "general"
    # The answers, then the caption call, in the order they were asked.
    answers = [m for m in provider.models]
    assert answers.count(VISION) == 3 and answers[-1] is None
    transcript = "\n".join(m["content"] if isinstance(m["content"], str) else ""
                           for m in provider.chat_calls[-1])
    assert "[Image attached: sign.jpg. fake reply]" in transcript
    stored = (await db_client.get(f"/chats/{chat}/messages")).json()
    assert stored[0]["metadata"]["images"][0]["caption"] == "fake reply"


async def test_deleting_the_chat_deletes_its_images(db_client, store):
    image = (await _upload(db_client, _jpeg_with_location())).json()
    chat = await _chat(db_client)
    await db_client.post(f"/chats/{chat}/messages", json={"content": "hi", "images": [image["id"]]})
    await _captions_written()
    path = store.file(DEMO_USER_ID, store.get(DEMO_USER_ID, uuid.UUID(image["id"])))
    assert path.exists()

    assert (await db_client.delete(f"/chats/{chat}")).status_code == 204

    assert not path.exists()
    assert (await db_client.get(f"/images/{image['id']}")).status_code == 404
