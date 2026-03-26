const MEMORY_SERVICE_URL = import.meta.env.VITE_MEMORY_SERVICE_URL || "http://localhost:8000";
const WS_URL = MEMORY_SERVICE_URL.replace(/^http/, "ws");

export interface GraphNode {
  id: string;
  name: string;
  type: string;
  created_at: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;
  label: string | null;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface GraphUpdateMessage {
  type: "graph_update" | "error";
  stats?: { entities_created: number; relationships_created: number };
  graph?: GraphData;
  message?: string;
}

export async function ingestMessage(
  message: string,
  userId: string,
  projectId: string
): Promise<void> {
  try {
    await fetch(`${MEMORY_SERVICE_URL}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, user_id: userId, project_id: projectId }),
    });
  } catch (error) {
    console.error("Memory ingest failed:", error);
  }
}

export async function fetchUserGraph(userId: string): Promise<GraphData> {
  const response = await fetch(`${MEMORY_SERVICE_URL}/graph/${userId}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch user graph: ${response.statusText}`);
  }
  return response.json();
}

export async function fetchProjectGraph(
  userId: string,
  projectId: string
): Promise<GraphData> {
  const response = await fetch(`${MEMORY_SERVICE_URL}/graph/${userId}/${projectId}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch project graph: ${response.statusText}`);
  }
  return response.json();
}

export function createGraphWebSocket(
  userId: string,
  onMessage: (data: GraphUpdateMessage) => void,
  onError?: (error: Event) => void
): WebSocket {
  const ws = new WebSocket(`${WS_URL}/ws/${userId}`);

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      onMessage(data);
    } catch (error) {
      console.error("Failed to parse WebSocket message:", error);
    }
  };

  ws.onerror = (error) => {
    console.error("WebSocket error:", error);
    onError?.(error);
  };

  return ws;
}
