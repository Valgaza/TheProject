import { X, FileText, Brain, Search, ShieldAlert, Database, AlertTriangle, BookOpen, ListChecks } from "lucide-react";
import { PIPELINE_TRACES, getDomain } from "@/lib/data";

const iconMap: Record<string, React.ReactNode> = {
  FileText: <FileText size={14} />,
  Brain: <Brain size={14} />,
  Search: <Search size={14} />,
  ShieldAlert: <ShieldAlert size={14} />,
  Database: <Database size={14} />,
  AlertTriangle: <AlertTriangle size={14} />,
  BookOpen: <BookOpen size={14} />,
  ListChecks: <ListChecks size={14} />,
};

interface PipelineTracePanelProps {
  chatId: string;
  onClose: () => void;
}

const PipelineTracePanel = ({ chatId, onClose }: PipelineTracePanelProps) => {
  const trace = PIPELINE_TRACES[chatId];
  if (!trace) return null;

  const domain = getDomain(trace.chatId === "chat-1" ? "medical" : trace.chatId === "chat-2" ? "legal" : trace.chatId === "chat-3" ? "financial" : "research");

  return (
    <div className="w-[280px] border-l border-nexus-border bg-nexus-bg h-full flex flex-col animate-slide-in-right overflow-y-auto scrollbar-thin">
      <div className="flex items-center justify-between px-4 py-3 border-b border-nexus-border">
        <span className="text-sm font-semibold text-nexus-text">Pipeline Trace</span>
        <button onClick={onClose} className="p-1 text-nexus-muted hover:text-nexus-text transition-colors duration-150">
          <X size={14} />
        </button>
      </div>

      <div className="p-4 space-y-5 text-xs">
        {/* Input Analysis */}
        <div>
          <h4 className="text-2xs uppercase tracking-widest text-nexus-muted mb-2">Input Analysis</h4>
          <div className="space-y-2">
            <div className="flex justify-between">
              <span className="text-nexus-muted">Input Type</span>
              <span className="px-2 py-0.5 rounded bg-nexus-surface text-nexus-text text-2xs">{trace.inputType}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-nexus-muted">Detected Intent</span>
              <span className="text-nexus-text">{trace.intent}</span>
            </div>
            <div>
              <div className="flex justify-between mb-1">
                <span className="text-nexus-muted">Confidence</span>
                <span className="text-nexus-text">{trace.confidence}%</span>
              </div>
              <div className="w-full h-1.5 bg-nexus-surface rounded-full overflow-hidden">
                <div className="h-full bg-nexus-indigo rounded-full transition-all duration-500" style={{ width: `${trace.confidence}%` }} />
              </div>
            </div>
          </div>
        </div>

        {/* Route */}
        <div>
          <h4 className="text-2xs uppercase tracking-widest text-nexus-muted mb-2">Route Selected</h4>
          <p className="text-nexus-text mb-3">Domain: {domain?.name}</p>
          <div className="space-y-0">
            {trace.steps.map((step, i) => (
              <div key={i} className="flex items-start gap-3 relative">
                <div className="flex flex-col items-center">
                  <div className={`w-6 h-6 rounded-full flex items-center justify-center border ${
                    step.status === "complete" ? "border-nexus-green text-nexus-green bg-nexus-green/10" :
                    step.status === "processing" ? "border-nexus-yellow text-nexus-yellow bg-nexus-yellow/10" :
                    "border-nexus-border text-nexus-muted"
                  }`}>
                    {iconMap[step.icon] || <FileText size={14} />}
                  </div>
                  {i < trace.steps.length - 1 && <div className="w-px h-6 bg-nexus-border" />}
                </div>
                <div className="pt-1">
                  <span className="text-nexus-text">{step.name}</span>
                  <span className={`ml-2 text-2xs ${
                    step.status === "complete" ? "text-nexus-green" :
                    step.status === "processing" ? "text-nexus-yellow" :
                    "text-nexus-muted"
                  }`}>
                    {step.status === "complete" ? "✓ Done" : step.status === "processing" ? "⟳ Running" : "Pending"}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Metrics */}
        <div>
          <h4 className="text-2xs uppercase tracking-widest text-nexus-muted mb-2">Performance Metrics</h4>
          <div className="space-y-2">
            {[
              ["Latency", trace.metrics.latency],
              ["Tokens Used", trace.metrics.tokens.toLocaleString()],
              ["Cost Estimate", trace.metrics.cost],
              ["Model Used", trace.metrics.model],
            ].map(([label, val]) => (
              <div key={label} className="flex justify-between">
                <span className="text-nexus-muted">{label}</span>
                <span className="text-nexus-text font-mono">{val}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

export default PipelineTracePanel;
