import { PROJECTS } from "@/lib/data";
import ProjectCard from "@/components/ProjectCard";

const ProjectsPage = () => {
  return (
    <div className="flex-1 overflow-y-auto scrollbar-thin p-6">
      <div className="max-w-3xl mx-auto">
        <p className="text-2xs text-nexus-muted mb-1">Nexus / Projects</p>
        <h1 className="text-xl font-semibold text-nexus-text mb-6">Projects</h1>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {PROJECTS.map((proj) => (
            <ProjectCard key={proj.id} project={proj} />
          ))}
        </div>
      </div>
    </div>
  );
};

export default ProjectsPage;
