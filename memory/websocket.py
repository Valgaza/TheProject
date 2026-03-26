from collections import defaultdict
from fastapi import WebSocket


class ConnectionManager:
    """Manages WebSocket connections per user for real-time graph updates."""

    def __init__(self):
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, user_id: str, websocket: WebSocket):
        """Accept and register a WebSocket connection for a user."""
        await websocket.accept()
        self._connections[user_id].add(websocket)

    def disconnect(self, user_id: str, websocket: WebSocket):
        """Remove a WebSocket connection for a user."""
        self._connections[user_id].discard(websocket)
        if not self._connections[user_id]:
            del self._connections[user_id]

    async def broadcast_to_user(self, user_id: str, message: dict):
        """Send a message to all WebSocket connections for a user."""
        dead_connections = []
        for websocket in self._connections.get(user_id, set()):
            try:
                await websocket.send_json(message)
            except Exception:
                dead_connections.append(websocket)

        # Clean up dead connections
        for ws in dead_connections:
            self.disconnect(user_id, ws)

    def get_user_connection_count(self, user_id: str) -> int:
        """Return number of active connections for a user."""
        return len(self._connections.get(user_id, set()))


manager = ConnectionManager()
