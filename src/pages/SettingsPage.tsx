import { useState } from "react";
import { useTheme } from "@/contexts/ThemeContext";
import { DOMAINS } from "@/lib/data";
import { Sun, Moon } from "lucide-react";

const SettingsPage = () => {
  const { theme, toggle } = useTheme();
  const [displayName, setDisplayName] = useState("Bhoumish Grover");
  const [defaultPipeline, setDefaultPipeline] = useState("general");

  return (
    <div className="flex-1 overflow-y-auto scrollbar-thin p-6">
      <div className="max-w-2xl mx-auto">
        <p className="text-2xs text-nexus-muted mb-1">Nexus / Settings</p>
        <h1 className="text-xl font-semibold text-nexus-text mb-6">Settings</h1>

        <div className="space-y-6">
          {/* Theme */}
          <div className="p-4 rounded-lg border border-nexus-border">
            <h2 className="text-sm font-medium text-nexus-text mb-3">Appearance</h2>
            <div className="flex items-center justify-between">
              <span className="text-sm text-nexus-text-secondary">Theme</span>
              <button
                onClick={toggle}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-nexus-border text-xs text-nexus-text hover:bg-nexus-surface transition-colors duration-150"
              >
                {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
                {theme === "dark" ? "Switch to Light" : "Switch to Dark"}
              </button>
            </div>
          </div>

          {/* Display Name */}
          <div className="p-4 rounded-lg border border-nexus-border">
            <h2 className="text-sm font-medium text-nexus-text mb-3">Profile</h2>
            <label className="block text-xs text-nexus-muted mb-1">Display Name</label>
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-nexus-border bg-nexus-surface text-sm text-nexus-text outline-none focus:shadow-[0_0_0_1px_hsl(var(--accent-indigo))] transition-shadow duration-150"
            />
          </div>

          {/* Default Pipeline */}
          <div className="p-4 rounded-lg border border-nexus-border">
            <h2 className="text-sm font-medium text-nexus-text mb-3">Default Pipeline</h2>
            <select
              value={defaultPipeline}
              onChange={(e) => setDefaultPipeline(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-nexus-border bg-nexus-surface text-sm text-nexus-text outline-none focus:shadow-[0_0_0_1px_hsl(var(--accent-indigo))] transition-shadow duration-150"
            >
              {DOMAINS.map((d) => (
                <option key={d.id} value={d.id}>{d.emoji} {d.name}</option>
              ))}
            </select>
          </div>

          {/* About */}
          <div className="p-4 rounded-lg border border-nexus-border">
            <h2 className="text-sm font-medium text-nexus-text mb-3">About NexusAI</h2>
            <p className="text-sm text-nexus-muted leading-relaxed">
              NexusAI is a Multi-Pipeline AI Orchestration System designed as a BTech final year project. 
              It addresses the challenge of managing multiple specialized AI models across different domains 
              by providing intelligent query routing, pipeline chaining, and transparent execution tracing. 
              Rather than forcing users to manually select models, NexusAI analyzes input intent and 
              automatically routes queries through optimal domain-specific pipelines — from medical 
              record analysis to legal contract review — while maintaining full visibility into the 
              decision-making process through real-time pipeline traces and performance metrics.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SettingsPage;
