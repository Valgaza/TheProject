"""Only core may call this service.

The browser used to reach this service directly, passing whichever user it
liked (or none: ingest took no user at all) -- so anyone could read or add to
anyone's documents. Core now makes every call after checking the
session cookie. This is the other half: a request that doesn't carry core's
shared secret is refused before it reaches a route.

Pure ASGI, matching memory's, so the two copies stay line-for-line comparable.

A copy of memory/nexus_memory/service_auth.py -- the graph and the corpus hold
the same people's data, so they sit behind the same check. Keep the two in step.
"""

from __future__ import annotations

import hmac
import os

HEADER = b"x-service-token"

# Liveness stays open: start.sh and docker health checks poll it, and it
# reveals nothing about anyone.
OPEN_PATHS = frozenset({"/api/health"})


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
