from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks, HTTPException
from pydantic import BaseModel

from .db import Neo4jClient
from .repository import MemoryRepository
from .extractor import Extractor
from .websocket import manager


class IngestRequest(BaseModel):
    message: str
    user_id: str
    project_id: str


class IngestResponse(BaseModel):
    status: str
    message: str


class GraphResponse(BaseModel):
    nodes: list[dict]
    edges: list[dict]


# Global instances
_db: Neo4jClient | None = None
_repository: MemoryRepository | None = None
_extractor: Extractor | None = None


def get_db() -> Neo4jClient:
    global _db
    if _db is None:
        _db = Neo4jClient()
    return _db


def get_repository() -> MemoryRepository:
    global _repository
    if _repository is None:
        _repository = MemoryRepository(get_db())
    return _repository


def get_extractor() -> Extractor:
    global _extractor
    if _extractor is None:
        _extractor = Extractor()
    return _extractor


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize connections
    get_db()
    get_repository()
    get_extractor()
    yield
    # Shutdown: close connections
    global _db
    if _db is not None:
        _db.close()
        _db = None


app = FastAPI(
    title="Memory Service",
    description="Entity extraction and graph storage API",
    version="0.1.0",
    lifespan=lifespan,
)


async def run_extraction_task(message: str, user_id: str, project_id: str):
    """Background task to extract entities and persist to Neo4j."""
    try:
        extractor = get_extractor()
        repository = get_repository()

        # Ensure user and project exist
        repository.create_user(user_id)
        repository.create_project(user_id, project_id, project_id)

        # Run extraction (CPU-bound, run in thread pool)
        loop = asyncio.get_event_loop()
        extraction = await loop.run_in_executor(None, extractor.extract, message)

        # Persist to Neo4j
        stats = await loop.run_in_executor(
            None,
            repository.persist_extraction,
            user_id,
            project_id,
            extraction
        )

        # Get updated graph for WebSocket notification
        graph = await loop.run_in_executor(
            None,
            repository.get_user_graph,
            user_id
        )

        # Notify connected WebSocket clients
        await manager.broadcast_to_user(user_id, {
            "type": "graph_update",
            "stats": stats,
            "graph": graph
        })

    except Exception as e:
        # Notify clients of error
        await manager.broadcast_to_user(user_id, {
            "type": "error",
            "message": str(e)
        })


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.post("/ingest", response_model=IngestResponse)
async def ingest(request: IngestRequest, background_tasks: BackgroundTasks):
    """
    Ingest a user message for entity extraction.

    Returns immediately, extraction runs in background.
    WebSocket clients will receive notification when complete.
    """
    background_tasks.add_task(
        run_extraction_task,
        request.message,
        request.user_id,
        request.project_id
    )
    return IngestResponse(
        status="accepted",
        message="Extraction queued for processing"
    )


@app.get("/graph/{user_id}", response_model=GraphResponse)
async def get_user_graph(user_id: str):
    """Get all nodes and edges for a user's graph."""
    try:
        repository = get_repository()
        loop = asyncio.get_event_loop()
        graph = await loop.run_in_executor(None, repository.get_user_graph, user_id)
        return GraphResponse(**graph)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/graph/{user_id}/{project_id}", response_model=GraphResponse)
async def get_project_graph(user_id: str, project_id: str):
    """Get all nodes and edges for a specific project's graph."""
    try:
        repository = get_repository()
        loop = asyncio.get_event_loop()
        graph = await loop.run_in_executor(
            None,
            repository.get_project_graph,
            user_id,
            project_id
        )
        return GraphResponse(**graph)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    """
    WebSocket endpoint for real-time graph updates.

    Connect to receive notifications when extraction completes
    and new nodes are written to Neo4j.
    """
    await manager.connect(user_id, websocket)
    try:
        while True:
            # Keep connection alive, handle incoming messages if needed
            data = await websocket.receive_text()
            # Echo back for ping/pong or future commands
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)
