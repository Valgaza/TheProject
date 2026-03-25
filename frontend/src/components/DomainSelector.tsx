import { useState, useRef, useEffect } from "react";
import { DOMAINS, getDomain } from "@/lib/data";
import { useChat } from "@/contexts/ChatContext";
import { ChevronDown, Check, Zap } from "lucide-react";

const DomainSelector = () => {
  const { selectedDomain, setSelectedDomain } = useChat();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const domain = getDomain(selectedDomain);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-full border border-nexus-border text-xs text-nexus-text hover:border-nexus-indigo transition-colors duration-150"
      >
        <span className="text-nexus-muted">Pipeline:</span>
        <span>{domain?.emoji} {domain?.name}</span>
        {selectedDomain === "general" && (
          <span className="flex items-center gap-0.5 text-nexus-indigo"><Zap size={10} /></span>
        )}
        <ChevronDown size={12} className={`transition-transform duration-150 ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-2 w-80 rounded-lg border border-nexus-border bg-nexus-elevated shadow-lg z-50 overflow-hidden animate-fade-in">
          {DOMAINS.map((d) => (
            <button
              key={d.id}
              onClick={() => { setSelectedDomain(d.id); setOpen(false); }}
              className={`w-full flex items-start gap-3 px-4 py-3 text-left transition-colors duration-150 hover:bg-nexus-surface ${
                selectedDomain === d.id ? "bg-nexus-surface" : ""
              }`}
            >
              <span className="text-lg mt-0.5">{d.emoji}</span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-nexus-text">{d.name}</span>
                  {d.id === "general" && (
                    <span className="text-2xs px-1.5 py-0.5 rounded bg-nexus-indigo/10 text-nexus-indigo">Recommended</span>
                  )}
                </div>
                <p className="text-2xs text-nexus-muted mt-0.5 leading-tight">{d.description}</p>
              </div>
              {selectedDomain === d.id && <Check size={14} className="text-nexus-indigo mt-1 flex-shrink-0" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

export default DomainSelector;
