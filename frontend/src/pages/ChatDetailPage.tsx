import { useParams } from "react-router-dom";
import { getChat, PIPELINE_TRACES } from "@/lib/data";
import DomainSelector from "@/components/DomainSelector";
import ChatWindow from "@/components/ChatWindow";
import ChatInput from "@/components/ChatInput";
import PipelineTracePanel from "@/components/PipelineTracePanel";
import { useChat } from "@/contexts/ChatContext";
import { GitBranch } from "lucide-react";

const ChatDetailPage = () => {
  const { id } = useParams<{ id: string }>();
  const { traceOpen, setTraceOpen } = useChat();
  const chat = getChat(id || "");

  if (!chat) {
    return (
      <div className="flex-1 flex items-center justify-center text-nexus-muted text-sm">
        Chat not found.
      </div>
    );
  }

  return (
    <div className="flex h-full">
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <div className="px-4 py-3 border-b border-nexus-border flex items-center justify-between">
          <div className="flex items-center gap-4">
            <h2 className="text-sm font-medium text-nexus-text">{chat.title}</h2>
            <DomainSelector />
          </div>
          <button
            onClick={() => setTraceOpen(!traceOpen)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-colors duration-150 ${
              traceOpen ? "bg-nexus-indigo/10 text-nexus-indigo" : "text-nexus-text-secondary hover:text-nexus-text hover:bg-nexus-surface"
            }`}
          >
            <GitBranch size={13} />
            Pipeline Trace
          </button>
        </div>

        <ChatWindow messages={chat.messages} />
        <ChatInput />
      </div>

      {traceOpen && id && PIPELINE_TRACES[id] && (
        <PipelineTracePanel chatId={id} onClose={() => setTraceOpen(false)} />
      )}
    </div>
  );
};

export default ChatDetailPage;
