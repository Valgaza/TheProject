"""Only core may call this service.

The browser used to reach memory directly, passing whichever ``user_id`` it
liked, and memory believed it -- so knowing someone's id was enough to read
or wipe their graph. Core now proxies every browser call after checking the
session cookie. This is the other half: a request that doesn't carry core's
shared secret is refused before it reaches a route.

Pure ASGI rather than ``@app.middleware("http")`` because the graph socket
needs the same check, and HTTP middleware never sees websocket scopes.

rag/rag_pipeline/service_auth.py is a copy of this for the RAG API. The two
services share no package, and forty lines is cheaper than inventing one.
"""

from __future__ import annotations

import hmac
import os

HEADER = b"x-service-token"

# Liveness stays open: start.sh and docker health checks poll it, and it
# reveals nothing about anyone.
OPEN_PATHS = frozenset({"/health"})


class ServiceTokenMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket") or scope["path"] in OPEN_PATHS:
            await self.app(scope, receive, send)
            return

        # Read per request, not at import: the app object is built before
        # settings load, and tests set the variable after importing.
        expected = os.environ.get("SERVICE_TOKEN", "").encode()
        sent = dict(scope["headers"]).get(HEADER, b"")
        # An unset token refuses everything rather than admitting everyone.
        if expected and hmac.compare_digest(sent, expected):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            # Closing before accept is how ASGI rejects a handshake (HTTP 403).
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
