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
import {
  CHATS_CHANGED,
  type Project,
  createProject,
  deleteProject,
  listProjects,
  updateProject,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const STORAGE_KEY = "nexus:current-project";

type ProjectContextValue = {
  projects: Project[];
  current: Project | null;
  loading: boolean;
  select: (projectId: string) => void;
  create: (input: { name: string; description?: string; domain?: string }) => Promise<Project>;
  /** Rename or pin/unpin. */
  update: (projectId: string, patch: { name?: string; description?: string; pinned?: boolean }) => Promise<Project>;
  /** Deletes the project and every chat inside it. If it was the current
   * one, falls back to another remaining project, or to none. */
  remove: (projectId: string) => Promise<void>;
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
  const { user, loading: authLoading } = useAuth();
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

  // This provider mounts on every route, including /login, before anyone
  // is signed in -- so it can't just fetch once on mount (that fetch would
  // 401, and nothing would ever ask again). Re-running when `user.id`
  // changes covers the first successful login and switching accounts;
  // logging out clears the list immediately rather than leaving the
  // previous person's projects on screen.
  useEffect(() => {
    if (authLoading) return;
    if (!user) {
      setProjects([]);
      setCurrentId(null);
      setLoading(false);
      return;
    }
    void refresh();
    // `user?.id` rather than `user`: AuthProvider's `refresh` builds a new
    // object on every call (even re-confirming the same account), which
    // would refire this effect on every one of those instead of only on an
    // actual identity change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authLoading, user?.id, refresh]);

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

  const update = useCallback(
    async (projectId: string, patch: { name?: string; description?: string; pinned?: boolean }) => {
      const updated = await updateProject(projectId, patch);
      setProjects(previous => previous.map(p => (p.id === projectId ? updated : p)));
      return updated;
    },
    []
  );

  const remove = useCallback(async (projectId: string) => {
    await deleteProject(projectId);
    // Two synchronous setState calls in the same tick -- React runs their
    // updaters in the order scheduled, so `remaining` (captured here) is
    // safely available by the time the second one reads it, without
    // needing `projects`/`currentId` themselves in this callback's closure.
    let remaining: Project[] = [];
    setProjects(previous => {
      remaining = previous.filter(p => p.id !== projectId);
      return remaining;
    });
    setCurrentId(previous => {
      if (previous !== projectId) return previous;
      const fallback = remaining[0]?.id ?? null;
      if (fallback) writeStored(fallback);
      return fallback;
    });
    window.dispatchEvent(new Event(CHATS_CHANGED));
  }, []);

  const value = useMemo<ProjectContextValue>(
    () => ({
      projects,
      current: projects.find(p => p.id === currentId) ?? null,
      loading,
      select,
      create,
      update,
      remove,
      refresh,
    }),
    [projects, currentId, loading, select, create, update, remove, refresh]
  );

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}

export function useProjects(): ProjectContextValue {
  const value = useContext(ProjectContext);
  if (!value) throw new Error("useProjects must be used inside ProjectProvider");
  return value;
}
