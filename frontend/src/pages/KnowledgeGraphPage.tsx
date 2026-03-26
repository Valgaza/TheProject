import GraphView from "@/components/GraphView";

const DEFAULT_USER_ID = "user_1";

const KnowledgeGraphPage = () => {
  return (
    <div className="flex-1 flex flex-col p-6">
      <div className="mb-4">
        <p className="text-2xs text-nexus-muted mb-1">Nexus / Knowledge Graph</p>
        <h1 className="text-xl font-semibold text-nexus-text">Global Knowledge Graph</h1>
        <p className="text-sm text-nexus-muted mt-1">
          All entities and relationships across your projects
        </p>
      </div>

      <div className="flex-1 rounded-lg border border-nexus-border bg-nexus-surface overflow-hidden min-h-[500px]">
        <GraphView userId={DEFAULT_USER_ID} title="Global Knowledge Graph" />
      </div>
    </div>
  );
};

export default KnowledgeGraphPage;
