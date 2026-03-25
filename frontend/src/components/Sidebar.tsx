import { useNavigate, useLocation } from "react-router-dom";
import { Plus, MessageSquare, FolderOpen, Settings, Stethoscope, Scale, BarChart3, BookOpen, GitBranch } from "lucide-react";
import ThemeToggle from "./ThemeToggle";
import { CHATS, PROJECTS, getDomain } from "@/lib/data";

const domainIcons: Record<string, React.ReactNode> = {
  medical: <Stethoscope size={14} />,
  legal: <Scale size={14} />,
  financial: <BarChart3 size={14} />,
  research: <BookOpen size={14} />,
};

const Sidebar = ({ collapsed }: { collapsed: boolean }) => {
  const navigate = useNavigate();
  const location = useLocation();

  if (collapsed) {
    return (
      <div className="w-14 border-r border-nexus-border bg-nexus-bg h-screen flex flex-col items-center py-4 gap-2">
        <div className="w-8 h-8 rounded-lg bg-nexus-indigo flex items-center justify-center mb-4">
          <GitBranch size={14} className="text-white" />
        </div>
        <button onClick={() => navigate("/chat")} className="p-2 rounded-lg hover:bg-nexus-surface text-nexus-muted hover:text-nexus-text transition-colors duration-150"><Plus size={16} /></button>
        <button onClick={() => navigate("/chat")} className={`p-2 rounded-lg transition-colors duration-150 ${location.pathname.startsWith("/chat") ? "bg-nexus-surface text-nexus-text" : "text-nexus-muted hover:text-nexus-text hover:bg-nexus-surface"}`}><MessageSquare size={16} /></button>
        <button onClick={() => navigate("/projects")} className={`p-2 rounded-lg transition-colors duration-150 ${location.pathname.startsWith("/projects") ? "bg-nexus-surface text-nexus-text" : "text-nexus-muted hover:text-nexus-text hover:bg-nexus-surface"}`}><FolderOpen size={16} /></button>
        <button onClick={() => navigate("/settings")} className={`p-2 rounded-lg transition-colors duration-150 ${location.pathname === "/settings" ? "bg-nexus-surface text-nexus-text" : "text-nexus-muted hover:text-nexus-text hover:bg-nexus-surface"}`}><Settings size={16} /></button>
      </div>
    );
  }

  return (
    <div className="w-[260px] border-r border-nexus-border bg-nexus-bg h-screen flex flex-col">
      {/* Logo */}
      <div className="px-4 py-4 flex items-center gap-2">
        <div className="w-7 h-7 rounded-lg bg-nexus-indigo flex items-center justify-center">
          <GitBranch size={13} className="text-white" />
        </div>
        <span className="text-lg font-medium text-nexus-text">
          Nexus<span className="text-nexus-indigo">AI</span>
        </span>
      </div>

      {/* New Chat */}
      <div className="px-3 mb-4">
        <button
          onClick={() => navigate("/chat")}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg border border-nexus-border text-sm text-nexus-text hover:bg-nexus-surface transition-colors duration-150"
        >
          <Plus size={14} />
          <span>New Chat</span>
          <span className="ml-auto text-2xs text-nexus-muted">⌘K</span>
        </button>
      </div>

      {/* Nav */}
      <div className="px-3 mb-3">
        {[
          { path: "/chat", icon: <MessageSquare size={14} />, label: "Chat" },
          { path: "/projects", icon: <FolderOpen size={14} />, label: "Projects" },
          { path: "/settings", icon: <Settings size={14} />, label: "Settings" },
        ].map((item) => (
          <button
            key={item.path}
            onClick={() => navigate(item.path)}
            className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-md text-xs transition-colors duration-150 mb-0.5 ${
              location.pathname.startsWith(item.path) ? "bg-nexus-surface text-nexus-text" : "text-nexus-text-secondary hover:text-nexus-text hover:bg-nexus-surface"
            }`}
          >
            {item.icon}
            {item.label}
          </button>
        ))}
      </div>

      {/* Recents */}
      <div className="px-3 flex-1 overflow-y-auto scrollbar-thin">
        <p className="text-2xs uppercase tracking-widest text-nexus-muted mb-2 px-3">Recents</p>
        {CHATS.map((chat) => {
          const domain = getDomain(chat.domainId);
          const isActive = location.pathname === `/chat/${chat.id}`;
          return (
            <button
              key={chat.id}
              onClick={() => navigate(`/chat/${chat.id}`)}
              className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-md text-xs transition-all duration-150 mb-0.5 group ${
                isActive ? "bg-nexus-surface text-nexus-text border-l-2 border-nexus-indigo" : "text-nexus-text-secondary hover:text-nexus-text hover:bg-nexus-surface"
              }`}
            >
              <span className="truncate flex-1 text-left">{chat.title}</span>
              <span className="text-2xs px-1.5 py-0.5 rounded bg-nexus-surface text-nexus-muted flex-shrink-0">
                {domain?.name}
              </span>
            </button>
          );
        })}

        <p className="text-2xs uppercase tracking-widest text-nexus-muted mb-2 px-3 mt-4">Projects</p>
        {PROJECTS.map((proj) => {
          const isActive = location.pathname === `/projects/${proj.id}`;
          return (
            <button
              key={proj.id}
              onClick={() => navigate(`/projects/${proj.id}`)}
              className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-md text-xs transition-all duration-150 mb-0.5 ${
                isActive ? "bg-nexus-surface text-nexus-text border-l-2 border-nexus-indigo" : "text-nexus-text-secondary hover:text-nexus-text hover:bg-nexus-surface"
              }`}
            >
              <span className="flex-shrink-0">{domainIcons[proj.domainId]}</span>
              <span className="truncate text-left">{proj.title}</span>
            </button>
          );
        })}
      </div>

      {/* Footer */}
      <div className="px-3 py-3 border-t border-nexus-border space-y-2">
        <ThemeToggle />
        <div className="flex items-center gap-2 px-3">
          <div className="w-2 h-2 rounded-full bg-nexus-green" />
          <span className="text-2xs text-nexus-muted">All pipelines operational</span>
        </div>
        <div className="flex items-center gap-2 px-3">
          <div className="w-7 h-7 rounded-full bg-nexus-surface flex items-center justify-center text-2xs font-medium text-nexus-text">
            BG
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-xs text-nexus-text truncate">Bhoumish Grover</p>
          </div>
          <button onClick={() => navigate("/settings")} className="p-1 text-nexus-muted hover:text-nexus-text transition-colors duration-150">
            <Settings size={12} />
          </button>
        </div>
        <p className="text-2xs text-nexus-muted text-center">NexusAI · BTech Final Year Project · 2025</p>
      </div>
    </div>
  );
};

export default Sidebar;
