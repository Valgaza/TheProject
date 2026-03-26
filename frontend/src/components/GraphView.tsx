import { useMemoryGraph } from "@/hooks/use-memory-graph";
import KnowledgeGraph from "./KnowledgeGraph";
import { RefreshCw, Wifi, WifiOff } from "lucide-react";

interface GraphViewProps {
  userId: string;
  projectId?: string;
  title?: string;
}

const GraphView = ({ userId, projectId, title }: GraphViewProps) => {
  const { graph, loading, error, connected, refresh } = useMemoryGraph({
    userId,
    projectId,
  });

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-nexus-border">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-medium text-nexus-text">
            {title || "Knowledge Graph"}
          </h3>
          <div className="flex items-center gap-1.5">
            {connected ? (
              <Wifi size={12} className="text-nexus-green" />
            ) : (
              <WifiOff size={12} className="text-nexus-muted" />
            )}
            <span className="text-2xs text-nexus-muted">
              {connected ? "Live" : "Disconnected"}
            </span>
          </div>
        </div>
        <button
          onClick={refresh}
          disabled={loading}
          className="flex items-center gap-1.5 px-2 py-1 rounded text-xs text-nexus-text-secondary hover:text-nexus-text hover:bg-nexus-surface transition-colors duration-150 disabled:opacity-50"
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      <div className="flex-1 p-4 overflow-hidden">
        {error ? (
          <div className="flex items-center justify-center h-full text-sm text-red-400">
            {error}
          </div>
        ) : loading && !graph.nodes.length ? (
          <div className="flex items-center justify-center h-full text-sm text-nexus-muted">
            Loading graph...
          </div>
        ) : (
          <KnowledgeGraph data={graph} width={700} height={450} />
        )}
      </div>

      {graph.nodes.length > 0 && (
        <div className="px-4 py-2 border-t border-nexus-border">
          <div className="flex items-center gap-4 text-2xs text-nexus-muted">
            <span>{graph.nodes.length} entities</span>
            <span>{graph.edges.length} relationships</span>
            <div className="flex items-center gap-2 ml-auto">
              {Object.entries({
                Person: "#6366f1",
                Technology: "#06b6d4",
                Organisation: "#f59e0b",
                Concept: "#8b5cf6",
              }).map(([type, color]) => (
                <div key={type} className="flex items-center gap-1">
                  <div
                    className="w-2 h-2 rounded-full"
                    style={{ backgroundColor: color }}
                  />
                  <span>{type}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default GraphView;
