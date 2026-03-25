import DomainSelector from "@/components/DomainSelector";
import ChatInput from "@/components/ChatInput";

const SUGGESTED = [
  { text: "Summarize this medical report", domain: "Medical" },
  { text: "Review this contract for red flags", domain: "Legal" },
  { text: "Analyze Q3 financial data", domain: "Financial" },
  { text: "Find key findings in this research paper", domain: "Research" },
  { text: "Explain this architecture diagram", domain: "Engineering" },
  { text: "What pipeline should I use?", domain: "General" },
];

const ChatPage = () => {
  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 border-b border-nexus-border flex items-center">
        <DomainSelector />
      </div>

      {/* Empty state */}
      <div className="flex-1 flex flex-col items-center justify-center px-4">
        <div className="max-w-2xl w-full text-center">
          <p className="text-2xs uppercase tracking-[0.2em] text-nexus-muted mb-4">Intelligent Pipeline Orchestration</p>
          <h1 className="text-2xl font-semibold text-nexus-text mb-3" style={{ lineHeight: "1.2" }}>
            What can I route for you?
          </h1>
          <p className="text-md text-nexus-muted max-w-lg mx-auto mb-8">
            I intelligently select and chain the best AI pipelines for your query — across medical, legal, financial, research, and more.
          </p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
            {SUGGESTED.map((s, i) => (
              <button
                key={i}
                className="text-left px-4 py-3 rounded-lg border border-nexus-border bg-nexus-surface text-xs text-nexus-text hover:border-nexus-indigo/40 hover:-translate-y-px transition-all duration-150 group"
              >
                <span className="block mb-1">{s.text}</span>
                <span className="text-2xs text-nexus-muted">{s.domain} pipeline</span>
              </button>
            ))}
          </div>
        </div>
      </div>

      <ChatInput />
    </div>
  );
};

export default ChatPage;
