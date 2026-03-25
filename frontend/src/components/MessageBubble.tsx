import PipelineBadge from "./PipelineBadge";
import type { Message } from "@/lib/data";

interface MessageBubbleProps {
  message: Message;
}

const MessageBubble = ({ message }: MessageBubbleProps) => {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div className="flex justify-end animate-fade-in">
        <div className="max-w-[70%] px-4 py-3 rounded-2xl bg-nexus-user-bubble text-nexus-text text-sm">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-3 animate-fade-in">
      <div className="flex-shrink-0 w-6 h-6 rounded-full bg-nexus-indigo mt-1" />
      <div className="flex-1 min-w-0">
        <div className="text-sm text-nexus-text whitespace-pre-wrap leading-relaxed">
          {message.content.split("\n").map((line, i) => {
            if (line.startsWith("**") && line.endsWith("**")) {
              return <p key={i} className="font-semibold text-lg mt-4 mb-1 first:mt-0">{line.replace(/\*\*/g, "")}</p>;
            }
            if (line.startsWith("- ")) {
              return <p key={i} className="pl-4 py-0.5">• {line.slice(2)}</p>;
            }
            if (line.match(/^\d+\./)) {
              return <p key={i} className="pl-4 py-0.5">{line}</p>;
            }
            if (line.startsWith("🔴") || line.startsWith("🟡") || line.startsWith("🟢")) {
              return <p key={i} className="pl-2 py-0.5">{line}</p>;
            }
            if (line.trim() === "") return <br key={i} />;
            return <p key={i}>{line}</p>;
          })}
        </div>
        {message.pipelineSteps && <PipelineBadge steps={message.pipelineSteps} />}
      </div>
    </div>
  );
};

export default MessageBubble;
