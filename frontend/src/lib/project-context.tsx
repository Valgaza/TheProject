"use client";

/**
 * Which project the app is currently looking at.
 *
 * It matters beyond navigation: a project is a memory scope. The graph panel
 * shows the union of the user tier and this project's, and a new chat inherits
 * it, so switching projects changes what the assistant can recall.
 *
 * The choice is kept in localStorage rather than the URL. It is a per-browser
 * preference, not a shareable address, and putting it in the URL would mean
 * every page carrying a query parameter it does not otherwise need.
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { CHATS_CHANGED, type Project, createProject, listProjects } from "@/lib/api";

const STORAGE_KEY = "nexus:current-project";

type ProjectContextValue = {
  projects: Project[];
  current: Project | null;
  loading: boolean;
  select: (projectId: string) => void;
  create: (input: { name: string; description?: string; domain?: string }) => Promise<Project>;
  refresh: () => Promise<void>;
};

const ProjectContext = createContext<ProjectContextValue | null>(null);

function readStored(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // Private windows and blocked site data both throw here.
    return null;
  }
}

function writeStored(id: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // Losing the preference is survivable; failing to render is not.
  }
}

export function ProjectProvider({ children }: { children: React.ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const loaded = await listProjects();
      setProjects(loaded);
      setCurrentId(previous => {
        const stored = previous ?? readStored();
        // A stored id that no longer exists must not strand the app on a
        // project that was deleted elsewhere.
        const valid = loaded.some(p => p.id === stored);
        return valid ? stored : (loaded[0]?.id ?? null);
      });
    } catch {
      setProjects([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const select = useCallback((projectId: string) => {
    setCurrentId(projectId);
    writeStored(projectId);
  }, []);

  const create = useCallback(
    async (input: { name: string; description?: string; domain?: string }) => {
      const project = await createProject(input);
      setProjects(previous => [project, ...previous]);
      select(project.id);
      // The rail lists chats for the current project, so it has to reload.
      window.dispatchEvent(new Event(CHATS_CHANGED));
      return project;
    },
    [select]
  );

  const value = useMemo<ProjectContextValue>(
    () => ({
      projects,
      current: projects.find(p => p.id === currentId) ?? null,
      loading,
      select,
      create,
      refresh,
    }),
    [projects, currentId, loading, select, create, refresh]
  );

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}

export function useProjects(): ProjectContextValue {
  const value = useContext(ProjectContext);
  if (!value) throw new Error("useProjects must be used inside ProjectProvider");
  return value;
}
