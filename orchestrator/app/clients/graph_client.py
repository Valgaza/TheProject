import httpx

from ..config import settings


async def get_graph(user_id: str, project_id: str | None = None) -> dict:
    """Ask the memory service (memory/app/main.py, GET /graph) for all the
    entity nodes and relationship edges it has stored for this user (and
    optionally scoped to one project). Returns the raw JSON response — see
    memory/app/main.py's GraphResponse for the exact shape (nodes, edges).
    """
    async with httpx.AsyncClient(timeout=settings.graph_timeout) as client:
        response = await client.get(
            f"{settings.memory_url}/graph",
            # httpx drops any param whose value is None instead of sending
            # it as the literal string "None", so it's safe to always pass
            # project_id here even when the caller didn't supply one.
            params={"user_id": user_id, "project_id": project_id},
        )
        response.raise_for_status()
        return response.json()
