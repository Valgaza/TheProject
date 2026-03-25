import { useState } from "react";
import { Paperclip, ArrowUp } from "lucide-react";

interface ChatInputProps {
  onSend?: (text: string) => void;
}

const ChatInput = ({ onSend }: ChatInputProps) => {
  const [value, setValue] = useState("");

  const handleSend = () => {
    if (!value.trim()) return;
    onSend?.(value.trim());
    setValue("");
  };

  return (
    <div className="border-t border-nexus-border bg-nexus-bg px-4 py-3">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-end gap-2 rounded-xl border border-nexus-border bg-nexus-surface px-3 py-2 focus-within:shadow-[0_0_0_1px_hsl(var(--accent-indigo))] transition-shadow duration-150">
          <button className="p-1.5 text-nexus-muted hover:text-nexus-text transition-colors duration-150">
            <Paperclip size={16} />
          </button>
          <textarea
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
            placeholder="Ask anything — Nexus will route it."
            rows={1}
            className="flex-1 bg-transparent text-sm text-nexus-text placeholder:text-nexus-muted resize-none outline-none max-h-32"
          />
          <button
            onClick={handleSend}
            disabled={!value.trim()}
            className={`p-1.5 rounded-lg transition-all duration-150 ${
              value.trim()
                ? "bg-nexus-indigo text-white shadow-[0_0_8px_hsl(var(--accent-indigo)/0.4)]"
                : "bg-nexus-surface text-nexus-muted"
            }`}
          >
            <ArrowUp size={16} />
          </button>
        </div>
        <p className="text-center text-2xs text-nexus-muted mt-2">
          Nexus auto-detects input type · Routes to optimal pipeline · End-to-end encrypted
        </p>
      </div>
    </div>
  );
};

export default ChatInput;
