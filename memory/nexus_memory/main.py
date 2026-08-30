import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
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
from .logging_config import configure_logging
from .scopes import Scope, read_groups, write_group

logger = logging.getLogger(__name__)

_graphiti: Graphiti | None = None


def _require_graphiti() -> Graphiti:
    """503 rather than 500 when startup failed.

    This used to be a bare ``assert``, which vanishes under ``python -O`` and
    reports a dead Neo4j connection as an internal error.
    """
    if _graphiti is None:
        raise HTTPException(status_code=503, detail="memory service is still starting")
    return _graphiti


# ── Connection manager ────────────────────────────────────────────────────────


@dataclass(eq=False)
class Subscription:
    """One websocket and the set of groups it wants to see.

    A subscriber watching a project asks for two groups, its project tier and
    the user tier. Keying subscriptions by a single group id (as this did
    before) meant a user-scope write never reached a project watcher: memory
    would form and the panel would not move.
    """

    websocket: WebSocket
    groups: frozenset[str] = field(default_factory=frozenset)


class ConnectionManager:
    def __init__(self) -> None:
        self._subscriptions: list[Subscription] = []

    async def connect(self, websocket: WebSocket, groups: list[str]) -> Subscription:
        await websocket.accept()
        subscription = Subscription(websocket=websocket, groups=frozenset(groups))
        self._subscriptions.append(subscription)
        logger.info(
            "WS connected  groups=%s total=%d", sorted(groups), len(self._subscriptions)
        )
        return subscription

    def disconnect(self, subscription: Subscription) -> None:
        try:
            self._subscriptions.remove(subscription)
        except ValueError:
            return
        logger.info("WS disconnected remaining=%d", len(self._subscriptions))

    def watchers_of(self, group_id: str) -> list[Subscription]:
        return [s for s in self._subscriptions if group_id in s.groups]

    async def send(self, subscription: Subscription, payload: str) -> None:
        try:
            await subscription.websocket.send_text(payload)
        except Exception:
            self.disconnect(subscription)


manager = ConnectionManager()


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _build_graph_response(groups: list[str]) -> "GraphResponse":
    """The union of several groups, as one graph."""
    graphiti = _require_graphiti()
    nodes: list[EntityNode] = await EntityNode.get_by_group_ids(graphiti.driver, groups)
    try:
        edges: list[EntityEdge] = await EntityEdge.get_by_group_ids(graphiti.driver, groups)
    except GroupsEdgesNotFoundError:
        # The node call returns [] for an empty group; the edge call raises.
        edges = []
    return GraphResponse(
        group_ids=groups,
        nodes=[
            NodeOut(
                id=n.uuid,
                name=n.name,
                type=n.labels[0] if n.labels else "Entity",
                summary=n.summary,
                attributes=n.attributes,
                group_id=n.group_id,
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
                group_id=e.group_id,
            )
            for e in edges
        ],
    )


async def _broadcast_write(group_id: str) -> None:
    """Push a fresh snapshot to everyone whose scope set contains this group.

    Each subscriber sees the union it asked for, not just the group that
    changed, so a project watcher's view stays whole when a user-scope write
    lands. Subscribers sharing a scope set share one query.
    """
    watchers = manager.watchers_of(group_id)
    if not watchers:
        return

    by_scope_set: dict[frozenset[str], list[Subscription]] = {}
    for subscription in watchers:
        by_scope_set.setdefault(subscription.groups, []).append(subscription)

    for groups, subscriptions in by_scope_set.items():
        try:
            graph = await _build_graph_response(sorted(groups))
        except Exception:
            logger.exception("Failed to build graph for groups=%s", sorted(groups))
            continue
        payload = graph.model_dump_json()
        for subscription in subscriptions:
            await manager.send(subscription, payload)


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _graphiti
    configure_logging()
    cfg = Settings()

    _graphiti = Graphiti(
        uri=cfg.neo4j_uri,
        user=cfg.neo4j_username,
        password=cfg.neo4j_password,
        llm_client=OpenAIClient(
            config=LLMConfig(
                api_key=cfg.openai_api_key,
                model=cfg.extraction_model,
                max_tokens=4096,
            ),
            reasoning="low",
        ),
        embedder=OpenAIEmbedder(
            config=OpenAIEmbedderConfig(
                api_key=cfg.openai_api_key,
                embedding_model=cfg.embedding_model,
                # Pinned in code, not left to graphiti's env-read default of
                # 1024, which silently slices every vector. See config.py.
                embedding_dim=cfg.embedding_dim,
            )
        ),
    )
    await _graphiti.build_indices_and_constraints()
    logger.info(
        "Memory service ready  embed=%s dim=%d extract=%s",
        cfg.embedding_model,
        cfg.embedding_dim,
        cfg.extraction_model,
    )
    yield
    await _graphiti.close()
    logger.info("Memory service shut down")


app = FastAPI(title="Nexus Memory Service", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


# ── Models ────────────────────────────────────────────────────────────────────


class IngestRequest(BaseModel):
    text: str
    user_id: uuid.UUID
    project_id: uuid.UUID | None = None
    # Explicit, never inferred. Defaulting this would make the tier a
    # side effect of whether a project id happened to be passed.
    scope: Scope = "user"
    # Who said it, when that is a meaningful question. Given, the episode is
    # dialogue and graphiti gets "<speaker>: <text>", its documented format for
    # message episodes. Omitted, the episode is a plain statement and the text
    # is passed as-is.
    #
    # This used to hardcode "user: " onto everything, which taught the graph
    # that a person literally named "user" was the subject of every fact.
    speaker: str | None = None


class IngestResponse(BaseModel):
    status: str
    group_id: str


class NodeOut(BaseModel):
    id: str
    name: str
    type: str
    summary: str
    attributes: dict
    # Which tier this came from, so the UI can say so.
    group_id: str


class EdgeOut(BaseModel):
    id: str
    source: str
    target: str
    label: str
    fact: str
    group_id: str


class GraphResponse(BaseModel):
    group_ids: list[str]
    nodes: list[NodeOut]
    edges: list[EdgeOut]


class HealthResponse(BaseModel):
    status: str
    neo4j: str


# ── Background task ───────────────────────────────────────────────────────────


async def _run_ingest(text: str, group_id: str, speaker: str | None = None) -> None:
    graphiti = _require_graphiti()
    if speaker:
        body = f"{speaker}: {text}"
        source = EpisodeType.message
        description = f"nexus dialogue ({group_id})"
    else:
        body = text
        source = EpisodeType.text
        description = f"nexus statement ({group_id})"
    try:
        await graphiti.add_episode(
            name="episode",
            episode_body=body,
            source=source,
            source_description=description,
            reference_time=datetime.now(timezone.utc),
            group_id=group_id,
        )
        logger.info("Ingested episode into group=%s", group_id)
    except Exception:
        logger.exception("Failed to ingest episode into group=%s", group_id)
        return

    try:
        await _broadcast_write(group_id)
    except Exception:
        logger.exception("Failed to broadcast update for group=%s", group_id)


# ── HTTP routes ───────────────────────────────────────────────────────────────


@app.post("/ingest", response_model=IngestResponse, status_code=202)
async def ingest(req: IngestRequest, background_tasks: BackgroundTasks):
    """Accept text for async ingestion into exactly one scope."""
    if not req.text.strip():
        raise HTTPException(status_code=422, detail="text must not be empty")
    try:
        group_id = write_group(req.scope, req.user_id, req.project_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    background_tasks.add_task(_run_ingest, req.text, group_id, req.speaker)
    return IngestResponse(status="accepted", group_id=group_id)


@app.get("/graph", response_model=GraphResponse)
async def get_graph(user_id: uuid.UUID, project_id: uuid.UUID | None = None):
    """The union of the user tier and, when given, one project tier."""
    return await _build_graph_response(read_groups(user_id, project_id))


@app.delete("/graph", status_code=204)
async def delete_graph(user_id: uuid.UUID, project_id: uuid.UUID | None = None):
    """Wipe every group this caller can read -- for demo resets only.

    Deliberately matches the read set rather than a single tier: this backs a
    reset button, and clearing half of what the panel shows would be worse than
    clearing all of it.
    """
    graphiti = _require_graphiti()
    groups = read_groups(user_id, project_id)
    await graphiti.driver.execute_query(
        "MATCH (n) WHERE n.group_id IN $groups DETACH DELETE n",
        groups=groups,
    )
    logger.info("Deleted graph for groups=%s", groups)

    empty = GraphResponse(group_ids=groups, nodes=[], edges=[])
    payload = empty.model_dump_json()
    for group_id in groups:
        for subscription in manager.watchers_of(group_id):
            await manager.send(subscription, payload)


@app.get("/health", response_model=HealthResponse)
async def health():
    """Liveness plus Neo4j reachability."""
    if _graphiti is None:
        return HealthResponse(status="starting", neo4j="unknown")
    try:
        records, _, _ = await _graphiti.driver.execute_query("RETURN 1 AS ok", routing_="r")
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
async def ws_graph(websocket: WebSocket, user_id: uuid.UUID, project_id: uuid.UUID | None = None):
    """Stream graph updates for one scope set.

    Sends the current union on connect, then a fresh snapshot whenever a write
    lands in *any* group the client asked for.
    """
    groups = read_groups(user_id, project_id)
    subscription = await manager.connect(websocket, groups)
    try:
        graph = await _build_graph_response(groups)
        await websocket.send_text(graph.model_dump_json())

        # Held open; inbound frames are not part of the protocol.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(subscription)
