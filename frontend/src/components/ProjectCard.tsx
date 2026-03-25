import { getDomain, type Project } from "@/lib/data";
import { Stethoscope, Scale, BarChart3, BookOpen } from "lucide-react";
import { useNavigate } from "react-router-dom";

const domainIcons: Record<string, React.ReactNode> = {
  medical: <Stethoscope size={18} />,
  legal: <Scale size={18} />,
  financial: <BarChart3 size={18} />,
  research: <BookOpen size={18} />,
};

interface ProjectCardProps {
  project: Project;
}

const ProjectCard = ({ project }: ProjectCardProps) => {
  const navigate = useNavigate();
  const domain = getDomain(project.domainId);

  return (
    <button
      onClick={() => navigate(`/projects/${project.id}`)}
      className="w-full text-left p-4 rounded-lg border border-nexus-border bg-nexus-surface hover:border-nexus-indigo/40 transition-all duration-150 hover:-translate-y-px group"
    >
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-nexus-bg text-nexus-muted group-hover:text-nexus-indigo transition-colors duration-150">
          {domainIcons[project.domainId] || <BookOpen size={18} />}
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-medium text-nexus-text truncate">{project.title}</h3>
          <p className="text-2xs text-nexus-muted mt-1 line-clamp-2">{project.description}</p>
          <div className="flex items-center gap-3 mt-3">
            <span className="text-2xs px-2 py-0.5 rounded-full border border-nexus-border text-nexus-text-secondary">
              {domain?.emoji} {domain?.name}
            </span>
            <span className="text-2xs text-nexus-muted">{project.chatIds.length} chat{project.chatIds.length !== 1 ? "s" : ""}</span>
            <span className="text-2xs text-nexus-muted">Last active {project.lastActive}</span>
          </div>
        </div>
      </div>
    </button>
  );
};

export default ProjectCard;
