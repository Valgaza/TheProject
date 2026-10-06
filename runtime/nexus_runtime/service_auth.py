"""Only core and memory may call this service.

The same check as memory/nexus_memory/service_auth.py, with one addition:
the token is also accepted as ``Authorization: Bearer <token>``. Core and
graphiti talk to this service through the OpenAI SDK, which sends its API key
that way, so setting ``OPENAI_API_KEY`` to the service token is all the
callers need.

Pure ASGI, matching memory's, so the copies stay comparable.
"""

from __future__ import annotations

import hmac
import os

HEADER = b"x-service-token"
AUTHORIZATION = b"authorization"

# Liveness stays open: start.sh polls it, and it reveals nothing about anyone.
OPEN_PATHS = frozenset({"/health"})


def _presented(headers: dict[bytes, bytes]) -> bytes:
    if HEADER in headers:
        return headers[HEADER]
    auth = headers.get(AUTHORIZATION, b"")
    scheme, _, token = auth.partition(b" ")
    return token.strip() if scheme.lower() == b"bearer" else b""


class ServiceTokenMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket") or scope["path"] in OPEN_PATHS:
            await self.app(scope, receive, send)
            return

        # Read per request, not at import, so tests can set it after importing.
        expected = os.environ.get("SERVICE_TOKEN", "").encode()
        sent = _presented(dict(scope["headers"]))
        # An unset token refuses everything rather than admitting everyone.
        if expected and hmac.compare_digest(sent, expected):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": b'{"detail":"missing or invalid service token"}',
        })
