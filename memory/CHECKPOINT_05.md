# Checkpoint 05 — Demo Hardening and Polish

## Demo Runbook (cold start → live demo)

### Prerequisites

- Docker Desktop running
- `.env` file at the project root (copy from `.env.example`)
- Python venv at `.venv/` (run `uv sync` if it doesn't exist)
- Node dependencies installed (`cd frontend && npm install`)

---

### Step 1 — Start backend services

Open **Terminal A** at the project root and run:

```bash
./start.sh
```

The script:
1. Checks `.env` exists
2. Starts Neo4j via `docker compose up -d`
3. Polls `http://localhost:7474/` until Neo4j responds (typically 20–40 s)
4. Starts the memory service with uvicorn on port 8000

Wait for this line before continuing:
```
INFO:     Memory service ready
INFO:     Application startup complete.
```

---

### Step 2 — Start the frontend

Open **Terminal B** at the project root:

```bash
cd frontend && npm run dev
```

Wait for:
```
▲ Next.js 16.2.6
- Local: http://localhost:3000
```

---

### Step 3 — Open the app

Open `http://localhost:3000` in the browser.

Navigate to `http://localhost:3000/graph`.

**What you should see:**
- While the backend is being queried: **"LOADING…"** text in the centre of the canvas, and **"Loading…"** in the inspector panel
- If no data has been ingested yet: **"Graph is empty — send a message to start building memory."** on the canvas
- Bottom-right chrome shows the connection status:
  - `● connecting` (grey) — WebSocket handshake in progress
  - `● live` (green) — WebSocket open and receiving
  - `● offline` (red) — WebSocket closed (memory service down)

---

### Step 4 — Ingest messages

Navigate to `http://localhost:3000/conversation` and send these messages
one at a time (Cmd+Enter or the Send button):

**Message 1:**
```
Sarah Chen is the Head of Engineering at Nexus Labs. She manages a team of twelve engineers.
```

**Message 2:**
```
Sarah is working on a new distributed caching layer called Prism. The project is behind schedule.
```

**Message 3:**
```
David Park is a senior engineer on Sarah's team. He is the tech lead for the Prism project.
```

Each message posts to `POST /ingest` immediately. The background processing takes
15–30 s (graphiti + OpenAI). Watch Terminal A for:
```
INFO:     Ingested episode for group=user_001
```

---

### Step 5 — Verify the graph

Switch back to `http://localhost:3000/graph`.

After all three ingestions complete, expect:
- **Nodes:** `Sarah Chen`, `Nexus Labs`, `Prism`, `David Park` (plus any derived
  entities the LLM extracts)
- **Edges** connecting them with relationship labels
- Node count and edge count updated in the top-left chrome

**Inspect a node:**
Click any node. The inspector panel shows:
- Node name, type (colour-coded), and graphiti summary
- UUID (first 8 chars)
- Connection count
- Timestamp of last graph push
- Clickable list of all connected nodes

Click a connected node in the list to jump to it.

---

### Step 6 — Verify live WebSocket push

Keep the graph page open. Send a fourth message from the conversation page:

```
Sarah Chen reports directly to the CEO, Marcus Webb.
```

Within ~20 s of ingestion completing, new nodes and edges appear on the graph
**without a page refresh**. The timestamp in the bottom-right chrome updates.

---

### Step 7 — Reset the graph

To wipe all data and run the demo again from scratch:

1. Click the **×** button in the top-right corner of the graph canvas.
2. This calls `DELETE /graph?user_id=user_001`, which deletes all Neo4j nodes
   for this user and broadcasts an empty graph snapshot to all connected clients.
3. The canvas immediately clears and shows the empty-state message.
4. Go back to the conversation page and send new messages.

Alternatively, call the endpoint directly:
```bash
curl -X DELETE "http://localhost:8000/graph?user_id=user_001"
```

---

### Stopping everything

- **Memory service:** Ctrl+C in Terminal A (uvicorn stops, Neo4j keeps running)
- **Frontend:** Ctrl+C in Terminal B
- **Neo4j:** `docker compose down` (data persists in the named volume)
- **Neo4j + wipe data:** `docker compose down -v` (destroys the volume)

---

## Context Summary

### What changed in this checkpoint

#### `memory/app/main.py`

1. **`DELETE /graph` endpoint** — wipes all Neo4j nodes for a given `group_id`
   using raw Cypher (`MATCH (n {group_id: $gid}) DETACH DELETE n`), then
   broadcasts an empty `GraphResponse` snapshot to connected WebSocket clients.
   Status 204 on success.

2. **CORS** — added `"DELETE"` to `allow_methods` so the browser can call the
   reset endpoint from the graph page.

#### `frontend/src/app/graph/page.tsx`

| Change | Detail |
|--------|--------|
| `loading` state | `useState(true)` — set `false` by `applyPayload` or fetch error handler. Canvas and inspector both render a "Loading…" state while true. |
| `wsStatus` state | `"connecting" \| "connected" \| "disconnected"` — driven by `ws.onopen` and `ws.onclose`. Bottom-right chrome dot changes colour: green = live, red = offline, grey = connecting. |
| `GNode.summary` field | Added to the type and populated from `ApiNode.summary` in `applyPayload`. |
| Inspector accuracy | Removed hardcoded confidence bar (0.86) and hardcoded "Mentioned in" section. Now shows the real `focusNode.summary` from graphiti (when non-empty) and real connection data. |
| Reset button | **×** button added to top-right chrome. Calls `DELETE /graph?user_id=user_001`, then clears all client-side graph state and the stable position map so the next ingest starts with a clean layout. |
| Empty-state text | SVG canvas renders "Graph is empty — send a message…" when `!loading && graphNodes.length === 0`. Previously this was only shown in the inspector panel. |

#### `start.sh` (new, project root)

Single script for demo startup. Usage: `./start.sh`

Sequence:
1. Validates `.env` exists
2. Validates Docker is available
3. Runs `docker compose up -d`
4. Polls `http://localhost:7474/` until Neo4j HTTP API responds
5. `cd memory && PYTHONPATH=. uvicorn app.main:app --reload --port 8000` (foreground)

The frontend is intentionally left out — it runs separately so the two processes
have independent logs.

---

### Final project state

```
TheProject/
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── .env                                    ← created by user, not committed
├── start.sh                                ← NEW: demo startup script
├── .venv/
├── frontend/
│   ├── .env.local
│   └── src/app/
│       ├── conversation/page.tsx           (unchanged from CP04)
│       └── graph/page.tsx                  ← UPDATED: loading, ws status, reset, real inspector
├── memory/
│   ├── app/
│   │   ├── config.py                       (unchanged)
│   │   └── main.py                         ← UPDATED: DELETE /graph, CORS
│   ├── CHECKPOINT_01.md … CHECKPOINT_04.md
│   └── CHECKPOINT_05.md                    ← NEW (this file)
├── pipelines/
└── shared/
```

### Hardcoded values still to update when auth is added

- `USER_ID = "user_001"` in `conversation/page.tsx` and `graph/page.tsx`
- No project scope — all messages go to the user-level graph
- `allow_origins=["http://localhost:3000"]` in CORS middleware
