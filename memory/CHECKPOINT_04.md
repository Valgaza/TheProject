# Checkpoint 04 — Frontend Connected to Memory Service

## End-to-End Testing Instructions

### Step 1 — Start all services

**Terminal A — Neo4j:**
```bash
docker compose up -d
docker compose ps   # wait until STATUS = healthy
```

**Terminal B — Memory service:**
```bash
cd memory
PYTHONPATH=. ../.venv/bin/uvicorn app.main:app --reload --port 8000
```

Wait for:
```
INFO:     Memory service ready
INFO:     Application startup complete.
```

**Terminal C — Frontend:**
```bash
cd frontend
npm run dev
```

Wait for:
```
▲ Next.js 16.2.6
- Local: http://localhost:3000
```

---

### Step 2 — Send test messages

Open `http://localhost:3000/conversation` in the browser.

Type the following messages one at a time, sending each with **Cmd+Enter** or
the Send button:

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

After sending each message you should see:
- The message appear immediately in the chat as a user bubble
- The mock AI response follow ~600ms later
- In Terminal B, after ~15–30 s: `INFO: Ingested episode for group=user_001`

---

### Step 3 — Open the graph page

Navigate to `http://localhost:3000/graph`.

On load the page fetches the current graph state over HTTP. If ingestion is
still running you may see an empty canvas ("Graph is empty.") — wait a few
seconds and refresh.

**After all three messages have been ingested you should see:**

- **Nodes** (exact names depend on what the LLM extracts, but expect):
  - `Sarah Chen`
  - `Nexus Labs`
  - `Prism` (or "Prism project")
  - `David Park`
  - possibly `engineering team`, `caching layer`, etc.

- **Edges** connecting them, e.g.:
  - `Sarah Chen` —MANAGES→ `engineering team`
  - `Sarah Chen` —WORKS_ON→ `Prism`
  - `David Park` —TECH_LEAD_OF→ `Prism`
  - `David Park` —WORKS_FOR→ `Sarah Chen`

- The **node count and edge count** in the top-left chrome will reflect the live
  numbers, not the old hardcoded 38/42.

- The **bottom-right live indicator** shows the wall-clock time of the last push
  (e.g. `● live · 14:32:07`).

---

### Step 4 — Verify the live WebSocket push

Keep `http://localhost:3000/graph` open in the browser. Switch back to the
conversation page and send a fourth message:

```
Sarah Chen reports directly to the CEO, Marcus Webb.
```

Watch the graph page — without refreshing, within ~20 seconds of the ingestion
completing, the graph should update automatically: new nodes (`Marcus Webb`,
possibly `CEO`) and new edges will appear.

If you want to observe the raw WebSocket push, open the browser dev tools
**Network** tab, filter by **WS**, click the `/ws/graph/user_001` connection,
and watch the **Messages** panel. Each push arrives as a full JSON snapshot.

---

### Step 5 — Inspect a node

Click any node on the graph canvas. The inspector panel on the right will show:
- The node name and type
- Its UUID (first 8 chars)
- The time of the last graph update
- All connected nodes listed as clickable relationships

---

## Context Summary

### What changed in the frontend

**`frontend/src/app/conversation/page.tsx`**

Added two constants at the top:
```typescript
const MEMORY_URL = process.env.NEXT_PUBLIC_MEMORY_URL ?? "http://localhost:8000";
const USER_ID = "user_001";
```

Modified `send()` to fire a `fetch` to `POST /ingest` before the mock AI
timeout. The call is fire-and-forget (`.catch` logs to console, never throws):
```typescript
fetch(`${MEMORY_URL}/ingest`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ text, user_id: USER_ID }),
}).catch(err => console.error("[memory] ingest failed:", err));
```
Everything else in the conversation page is unchanged.

**`frontend/src/app/graph/page.tsx`**

Replaced the hardcoded `GRAPH` constant with live state:

| Old | New |
|-----|-----|
| `const GRAPH = (() => {...})()` | `const [graphNodes, setGraphNodes] = useState<GNode[]>([])` |
| Static x/y positions in source | `positionsRef` — ring layout assigned once per node ID, stable across updates |
| None | `useEffect` fetches `GET /graph?user_id=user_001` on mount |
| None | `useEffect` opens `WebSocket /ws/graph/user_001` and calls `applyPayload` on each push |
| `GRAPH.nodes`, `GRAPH.edges` | `graphNodes`, `graphEdges` (same shape used by all SVG rendering) |
| Hardcoded pulse IDs | Pulse cycles through live node IDs via `nodesRef` (stable interval, no re-subscription) |
| `"● live · 47ms ago"` | `"● live · {lastUpdated}"` — actual wall-clock time of last update |
| `setPinned("q3")` default | `setPinned(null)` — no hardcoded initial selection |

The entire SVG rendering block, all CSS classes, all animations, all transitions,
and the inspector structure are **identical** to the original. Only the data
source changed.

### Node layout

Nodes are placed in concentric rings centred on the SVG midpoint (550, 360):

| Ring | Capacity | Radius |
|------|----------|--------|
| 0    | 1 node   | 0 (centre) |
| 1    | 6 nodes  | 155px |
| 2    | 12 nodes | 290px |
| 3    | 18 nodes | 410px |

Positions are assigned once (stored in `positionsRef`) so WebSocket updates
never cause nodes to jump.

### How the frontend communicates with the memory service

| Direction | Protocol | Endpoint | When |
|-----------|----------|----------|------|
| Frontend → Backend | HTTP POST | `/ingest` | Every time user sends a chat message |
| Frontend → Backend | HTTP GET | `/graph?user_id=user_001` | On graph page mount |
| Backend → Frontend | WebSocket | `/ws/graph/user_001` | Persistent; pushes full graph snapshot after each ingestion |

### Configuration

`frontend/.env.local`:
```
NEXT_PUBLIC_MEMORY_URL=http://localhost:8000
```

To point at a different host/port, change this value and restart `npm run dev`.
The `ws://` WebSocket URL is derived automatically from the HTTP URL by replacing
`http` with `ws`.

### Hardcoded values (to update when auth is added)

- `USER_ID = "user_001"` — in both `conversation/page.tsx` and `graph/page.tsx`
- No project scope — all messages go to the user-level graph

### Current project state

```
TheProject/
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── .venv/
├── frontend/
│   ├── .env.local                          ← NEW
│   └── src/
│       └── app/
│           ├── conversation/page.tsx       ← UPDATED: fetch to /ingest
│           └── graph/page.tsx              ← UPDATED: live data via HTTP + WS
├── memory/
│   ├── app/
│   │   ├── config.py
│   │   └── main.py
│   ├── CHECKPOINT_01.md
│   ├── CHECKPOINT_02.md
│   ├── CHECKPOINT_03.md
│   └── CHECKPOINT_04.md                   ← NEW (this file)
├── pipelines/
└── shared/
```
