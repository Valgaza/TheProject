import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from graphiti_core import Graphiti
from graphiti_core.edges import EntityEdge
from graphiti_core.embedder import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.errors import GroupsEdgesNotFoundError
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_client import OpenAIClient
from graphiti_core.nodes import EntityNode, EpisodeType

from .config import Settings

logger = logging.getLogger(__name__)

_graphiti: Graphiti | None = None


# ── Connection manager ────────────────────────────────────────────────────────


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, group_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections[group_id].append(ws)
        logger.info(
            "WS connected  group=%s total=%d", group_id, len(self._connections[group_id])
        )

    def disconnect(self, group_id: str, ws: WebSocket) -> None:
        bucket = self._connections.get(group_id, [])
        try:
            bucket.remove(ws)
        except ValueError:
            pass
        logger.info("WS disconnected group=%s remaining=%d", group_id, len(bucket))

    async def broadcast(self, group_id: str, payload: str) -> None:
        bucket = self._connections.get(group_id, [])
        if not bucket:
            return
        dead: list[WebSocket] = []
        for ws in bucket:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(group_id, ws)


manager = ConnectionManager()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _group_id(user_id: str, project_id: str | None) -> str:
    return f"{user_id}_{project_id}" if project_id else user_id


async def _build_graph_response(gid: str) -> "GraphResponse":
    assert _graphiti is not None
    nodes: list[EntityNode] = await EntityNode.get_by_group_ids(_graphiti.driver, [gid])
    try:
        edges: list[EntityEdge] = await EntityEdge.get_by_group_ids(_graphiti.driver, [gid])
    except GroupsEdgesNotFoundError:
        edges = []
    return GraphResponse(
        group_id=gid,
        nodes=[
            NodeOut(
                id=n.uuid,
                name=n.name,
                type=n.labels[0] if n.labels else "Entity",
                summary=n.summary,
                attributes=n.attributes,
            )
            for n in nodes
        ],
        edges=[
            EdgeOut(
                id=e.uuid,
                source=e.source_node_uuid,
                target=e.target_node_uuid,
                label=e.name,
                fact=e.fact,
            )
            for e in edges
        ],
    )


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _graphiti
    cfg = Settings()

    _graphiti = Graphiti(
        uri=cfg.neo4j_uri,
        user=cfg.neo4j_username,
        password=cfg.neo4j_password,
        llm_client=OpenAIClient(
            config=LLMConfig(
                api_key=cfg.openai_api_key,
                model="gpt-5.4-nano-2026-03-17",
                max_tokens=4096,
            ),
            reasoning="low",
        ),
        embedder=OpenAIEmbedder(
            config=OpenAIEmbedderConfig(api_key=cfg.openai_api_key)
        ),
    )
    await _graphiti.build_indices_and_constraints()
    logger.info("Memory service ready")
    yield
    await _graphiti.close()
    logger.info("Memory service shut down")


app = FastAPI(title="Nexus Memory Service", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


# ── Models ────────────────────────────────────────────────────────────────────


class IngestRequest(BaseModel):
    text: str
    user_id: str
    project_id: str | None = None


class IngestResponse(BaseModel):
    status: str


class NodeOut(BaseModel):
    id: str
    name: str
    type: str
    summary: str
    attributes: dict


class EdgeOut(BaseModel):
    id: str
    source: str
    target: str
    label: str
    fact: str


class GraphResponse(BaseModel):
    group_id: str
    nodes: list[NodeOut]
    edges: list[EdgeOut]


class HealthResponse(BaseModel):
    status: str
    neo4j: str


# ── Background task ───────────────────────────────────────────────────────────


async def _run_ingest(text: str, user_id: str, project_id: str | None) -> None:
    assert _graphiti is not None
    gid = _group_id(user_id, project_id)
    try:
        await _graphiti.add_episode(
            name="message",
            episode_body=f"user: {text}",
            source=EpisodeType.message,
            source_description="nexus chat message",
            reference_time=datetime.now(timezone.utc),
            group_id=gid,
        )
        logger.info("Ingested episode for group=%s", gid)
    except Exception:
        logger.exception("Failed to ingest episode for group=%s", gid)
        return

    # Broadcast updated graph to all connected WebSocket clients for this group.
    try:
        graph = await _build_graph_response(gid)
        await manager.broadcast(gid, graph.model_dump_json())
    except Exception:
        logger.exception("Failed to broadcast graph update for group=%s", gid)


# ── HTTP routes ───────────────────────────────────────────────────────────────


@app.post("/ingest", response_model=IngestResponse, status_code=202)
async def ingest(req: IngestRequest, background_tasks: BackgroundTasks):
    """Accept a chat message for async ingestion into the knowledge graph."""
    if not req.text.strip():
        raise HTTPException(status_code=422, detail="text must not be empty")
    background_tasks.add_task(_run_ingest, req.text, req.user_id, req.project_id)
    return IngestResponse(status="accepted")


@app.get("/graph", response_model=GraphResponse)
async def get_graph(user_id: str, project_id: str | None = None):
    """Return all entity nodes and edges for a user (optionally scoped to a project)."""
    assert _graphiti is not None
    return await _build_graph_response(_group_id(user_id, project_id))


@app.delete("/graph", status_code=204)
async def delete_graph(user_id: str, project_id: str | None = None):
    """Wipe all nodes and edges for a user — for demo resets only."""
    assert _graphiti is not None
    gid = _group_id(user_id, project_id)
    await _graphiti.driver.execute_query(
        "MATCH (n {group_id: $gid}) DETACH DELETE n",
        gid=gid,
    )
    logger.info("Deleted graph for group=%s", gid)
    empty = GraphResponse(group_id=gid, nodes=[], edges=[])
    await manager.broadcast(gid, empty.model_dump_json())


@app.get("/health", response_model=HealthResponse)
async def health():
    """Liveness + Neo4j reachability check."""
    if _graphiti is None:
        return HealthResponse(status="starting", neo4j="unknown")
    try:
        records, _, _ = await _graphiti.driver.execute_query(
            "RETURN 1 AS ok", routing_="r"
        )
        neo4j_status = "ok" if records else "unreachable"
    except Exception as exc:
        logger.error("Neo4j health check failed: %s", exc)
        neo4j_status = "unreachable"
    return HealthResponse(
        status="ok" if neo4j_status == "ok" else "degraded",
        neo4j=neo4j_status,
    )


# ── WebSocket route ───────────────────────────────────────────────────────────


@app.websocket("/ws/graph/{user_id}")
async def ws_graph(websocket: WebSocket, user_id: str, project_id: str | None = None):
    """
    Stream real-time graph updates to a client.

    Sends the current graph state immediately on connect, then pushes a fresh
    snapshot whenever ingestion completes for this user/project scope.
    """
    gid = _group_id(user_id, project_id)
    await manager.connect(gid, websocket)
    try:
        # Push the current state immediately so the client doesn't have to poll.
        graph = await _build_graph_response(gid)
        await websocket.send_text(graph.model_dump_json())

        # Hold the connection open; we don't process inbound messages.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(gid, websocket)
