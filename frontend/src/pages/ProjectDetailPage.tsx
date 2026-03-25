import { useParams, useNavigate } from "react-router-dom";
import { getProject, getDomain, CHATS } from "@/lib/data";
import { Plus, MessageSquare } from "lucide-react";

const ProjectDetailPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const project = getProject(id || "");

  if (!project) {
    return <div className="flex-1 flex items-center justify-center text-nexus-muted text-sm">Project not found.</div>;
  }

  const domain = getDomain(project.domainId);
  const projectChats = CHATS.filter((c) => project.chatIds.includes(c.id));

  return (
    <div className="flex-1 overflow-y-auto scrollbar-thin p-6">
      <div className="max-w-3xl mx-auto">
        <p className="text-2xs text-nexus-muted mb-1">Nexus / Projects / {project.title}</p>
        <div className="flex items-center gap-3 mb-2">
          <h1 className="text-xl font-semibold text-nexus-text">{project.title}</h1>
          <span className="text-2xs px-2 py-0.5 rounded-full border border-nexus-border text-nexus-text-secondary">
            {domain?.emoji} {domain?.name}
          </span>
        </div>
        <p className="text-sm text-nexus-muted mb-6">{project.description}</p>

        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-medium text-nexus-text">Chats</h2>
          <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs border border-nexus-border text-nexus-text hover:bg-nexus-surface transition-colors duration-150">
            <Plus size={12} />
            New Chat in Project
          </button>
        </div>

        <div className="space-y-2">
          {projectChats.map((chat) => (
            <button
              key={chat.id}
              onClick={() => navigate(`/chat/${chat.id}`)}
              className="w-full flex items-center gap-3 px-4 py-3 rounded-lg border border-nexus-border bg-nexus-surface hover:border-nexus-indigo/40 transition-all duration-150 text-left"
            >
              <MessageSquare size={14} className="text-nexus-muted flex-shrink-0" />
              <span className="text-sm text-nexus-text">{chat.title}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};

export default ProjectDetailPage;
