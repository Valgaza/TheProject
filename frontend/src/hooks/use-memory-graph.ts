import { useEffect, useState, useRef, useCallback } from "react";
import type { GraphData, GraphUpdateMessage } from "@/lib/memory-api";
import {
  fetchUserGraph,
  fetchProjectGraph,
  createGraphWebSocket,
} from "@/lib/memory-api";

interface UseMemoryGraphOptions {
  userId: string;
  projectId?: string;
}

interface UseMemoryGraphResult {
  graph: GraphData;
  loading: boolean;
  error: string | null;
  connected: boolean;
  refresh: () => Promise<void>;
}

export function useMemoryGraph({
  userId,
  projectId,
}: UseMemoryGraphOptions): UseMemoryGraphResult {
  const [graph, setGraph] = useState<GraphData>({ nodes: [], edges: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  const fetchGraph = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = projectId
        ? await fetchProjectGraph(userId, projectId)
        : await fetchUserGraph(userId);
      setGraph(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch graph");
    } finally {
      setLoading(false);
    }
  }, [userId, projectId]);

  useEffect(() => {
    fetchGraph();
  }, [fetchGraph]);

  useEffect(() => {
    const handleMessage = (data: GraphUpdateMessage) => {
      if (data.type === "graph_update" && data.graph) {
        if (projectId) {
          fetchGraph();
        } else {
          setGraph(data.graph);
        }
      } else if (data.type === "error") {
        console.error("Graph update error:", data.message);
      }
    };

    const handleError = () => {
      setConnected(false);
    };

    const ws = createGraphWebSocket(userId, handleMessage, handleError);

    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);

    wsRef.current = ws;

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [userId, projectId, fetchGraph]);

  return { graph, loading, error, connected, refresh: fetchGraph };
}
