# Checkpoint 03 — Real-Time Graph Updates via WebSocket

## Testing Instructions

### Prerequisites

Neo4j running and the service started:
```bash
docker compose up -d
cd memory
PYTHONPATH=. ../.venv/bin/uvicorn app.main:app --reload --port 8000
```

---

### Step 1 — Open a WebSocket connection

You need two terminal windows: one to hold the WebSocket open, one to fire HTTP
requests.

**Terminal A — connect with websocat:**
```bash
websocat ws://localhost:8000/ws/graph/user_001
```

On connect you will immediately receive the current graph state as a JSON object
(same shape as `GET /graph`). If the graph is empty it will look like:
```json
{"group_id":"user_001","nodes":[],"edges":[]}
```
If you already have data from previous testing it will contain the existing nodes
and edges. Either way, leave this terminal open — every push update will appear
here as a new line.

**To watch a project-scoped graph instead:**
```bash
websocat "ws://localhost:8000/ws/graph/user_001?project_id=nexus"
```

---

### Step 2 — Trigger an ingestion from a second terminal

**Terminal B:**
```bash
curl -s -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"text": "Bob is the CTO of Acme Corp and reports to the board.", "user_id": "user_001"}' \
  | python3 -m json.tool
```

Expected HTTP response (immediate):
```json
{
    "status": "accepted"
}
```

---

### Step 3 — Observe the push arriving in Terminal A

Wait ~15–30 seconds for the background LLM extraction to complete. You will see
the server log in the uvicorn terminal print:
```
INFO:     Ingested episode for group=user_001
INFO:     WS connected  group=user_001 total=1   (if you only have one client)
```

Then, without any action on your part, a new JSON object will appear in Terminal A:

```json
{
  "group_id": "user_001",
  "nodes": [
    {"id": "...", "name": "Alice",    "type": "Entity", "summary": "...", "attributes": {}},
    {"id": "...", "name": "Acme Corp","type": "Entity", "summary": "...", "attributes": {}},
    {"id": "...", "name": "Bob",      "type": "Entity", "summary": "...", "attributes": {}},
    ...
  ],
  "edges": [
    {"id": "...", "source": "...", "target": "...", "label": "IS_CTO_OF", "fact": "Bob is the CTO of Acme Corp."},
    ...
  ]
}
```

This is a full snapshot of the graph after ingestion — the same payload that
`GET /graph?user_id=user_001` would return at that moment.

---

### Step 4 — Verify multiple simultaneous clients

Open a second websocat in Terminal C while Terminal A is still connected:
```bash
websocat ws://localhost:8000/ws/graph/user_001
```

The server log will show `total=2`. Now fire another ingest from Terminal B:
```bash
curl -s -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"text": "Bob is building a new internal tool called Nexus for the engineering team.", "user_id": "user_001"}' \
  | python3 -m json.tool
```

After ingestion completes, both Terminal A and Terminal C will receive the same
push simultaneously.

---

### Step 5 — Verify project isolation

Connect to the project-scoped endpoint in Terminal D:
```bash
websocat "ws://localhost:8000/ws/graph/user_001?project_id=nexus"
```

Now send a user-level ingest (no `project_id`) from Terminal B. The update will
arrive in Terminals A and C but **not** in Terminal D — the project graph is
isolated from the user graph.

Confirm by sending a project-scoped ingest:
```bash
curl -s -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"text": "The Nexus tool needs a settings page.", "user_id": "user_001", "project_id": "nexus"}' \
  | python3 -m json.tool
```

This time only Terminal D receives the push.

---

### Browser console alternative (no wscat/websocat required)

Open the browser developer tools on any tab and paste:

```javascript
const ws = new WebSocket('ws://localhost:8000/ws/graph/user_001');
ws.onmessage = (e) => console.log('graph update:', JSON.parse(e.data));
ws.onopen = () => console.log('connected');
ws.onclose = () => console.log('disconnected');
```

You will see the initial graph snapshot logged immediately on `onopen`, then each
subsequent push logged as it arrives.

---

## Context Summary

### How it works

```
POST /ingest
    │
    └─► BackgroundTasks._run_ingest()
            │
            ├─► graphiti.add_episode()   ← LLM extraction + Neo4j write
            │
            └─► ConnectionManager.broadcast(group_id, graph_json)
                    │
                    └─► ws.send_text()  for each connected client
```

1. The HTTP `POST /ingest` returns 202 immediately and schedules `_run_ingest` as a background task.
2. When graphiti finishes writing to Neo4j, `_run_ingest` calls `_build_graph_response()` to read the updated state back from Neo4j.
3. It then calls `manager.broadcast()`, which sends the JSON payload to every WebSocket connection registered for that `group_id`.
4. If any connection is broken (client closed the tab, network drop), `broadcast` catches the send failure, marks that socket as dead, and removes it — no crash, no stale entries.

### WebSocket endpoint

| | |
|---|---|
| **URL** | `ws://localhost:8000/ws/graph/{user_id}` |
| **Query param** | `?project_id=<id>` (optional, same scoping as HTTP endpoints) |
| **On connect** | Sends current full graph snapshot immediately |
| **On ingest complete** | Pushes fresh full graph snapshot to all connected clients for that scope |
| **Message format** | Identical to `GET /graph` response — `{group_id, nodes[], edges[]}` |
| **Inbound messages** | Ignored — connection is receive-only from the client perspective |
| **Disconnect handling** | `WebSocketDisconnect` caught in `finally` block; `broadcast` also prunes dead sockets |

### Connection management

`ConnectionManager` maintains a `dict[group_id → list[WebSocket]]`. Each user/project
scope is tracked independently, so a broadcast for `user_001` never touches
connections watching `user_001_nexus`.

### Shared graph-building logic

`_build_graph_response(gid)` is now a standalone async helper used by both
`GET /graph` and the WebSocket broadcast. The HTTP endpoint and the WebSocket
push are guaranteed to return identically-shaped data.

### Complete endpoint reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness + Neo4j reachability |
| `POST` | `/ingest` | Accept a message for async graph ingestion (HTTP 202) |
| `GET` | `/graph` | Return current nodes + edges for a user/project scope |
| `WS` | `/ws/graph/{user_id}` | Stream real-time graph snapshots; `?project_id=` optional |

### Current project state

```
TheProject/
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── .venv/
├── frontend/
├── memory/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   └── main.py          ← ConnectionManager + WS endpoint + _build_graph_response
│   ├── pyproject.toml
│   ├── CHECKPOINT_01.md
│   ├── CHECKPOINT_02.md
│   └── CHECKPOINT_03.md     ← this file
├── pipelines/
└── shared/
```
