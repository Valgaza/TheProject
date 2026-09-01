"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Icon } from "./icons";
import { CHATS_CHANGED, type Chat, listAllChats } from "@/lib/api";
import { useProjects } from "@/lib/project-context";

/** "/c/abc" -> "c", "/" -> "landing". Only the first segment names a page. */
const getPageId = (path: string) => {
  const [segment] = path.split("/").filter(Boolean);
  return segment ?? "landing";
};

/** Which projects the user has expanded. Survives navigation, not reloads. */
const EXPANDED_KEY = "nexus:expanded-projects";

function readExpanded(): string[] {
  try {
    return JSON.parse(window.sessionStorage.getItem(EXPANDED_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function writeExpanded(ids: string[]): void {
  try {
    window.sessionStorage.setItem(EXPANDED_KEY, JSON.stringify(ids));
  } catch {
    // Losing which folders were open is survivable; failing to render is not.
  }
}

export const RailNav = () => {
  const pathname = usePathname();
  const router = useRouter();
  const { projects, current, select } = useProjects();

  const page = getPageId(pathname);

  const main = [
    { id: "projects", label: "Projects",        icon: "folder", path: "/projects" },
    { id: "graph",    label: "Knowledge graph", icon: "graph",  path: "/graph" },
    { id: "settings", label: "Settings",        icon: "cog",    path: "/settings" },
  ];

  // One fetch for every chat, grouped here. The rail used to load chats for the
  // current project only, which is why nothing else could ever be listed.
  const [chats, setChats] = useState<Chat[]>([]);
  const [expanded, setExpanded] = useState<string[]>([]);

  useEffect(() => {
    setExpanded(readExpanded());
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const loaded = await listAllChats();
        if (!cancelled) setChats(loaded);
      } catch {
        if (!cancelled) setChats([]);
      }
    };
    void load();
    window.addEventListener(CHATS_CHANGED, load);
    return () => {
      cancelled = true;
      window.removeEventListener(CHATS_CHANGED, load);
    };
  }, [pathname]);

  const byProject = useMemo(() => {
    const grouped = new Map<string, Chat[]>();
    for (const chat of chats) {
      if (!chat.project_id) continue;
      const existing = grouped.get(chat.project_id);
      if (existing) existing.push(chat);
      else grouped.set(chat.project_id, [chat]);
    }
    return grouped;
  }, [chats]);

  const loose = useMemo(() => chats.filter(c => !c.project_id), [chats]);

  const toggle = useCallback((projectId: string) => {
    setExpanded(previous => {
      const next = previous.includes(projectId)
        ? previous.filter(id => id !== projectId)
        : [...previous, projectId];
      writeExpanded(next);
      return next;
    });
  }, []);

  // Opening a project expands it, makes it current — the graph and any chat
  // started from it follow — and does not navigate. Navigating away from a tree
  // every time you open a folder is the thing that makes trees annoying.
  const openProject = useCallback(
    (projectId: string) => {
      select(projectId);
      if (!expanded.includes(projectId)) toggle(projectId);
    },
    [expanded, select, toggle]
  );

  return (
    <aside className="rail">
      <Link href="/" className="rail__brand" style={{ textDecoration: "none", color: "inherit" }}>
        <div className="rail__mark">N</div>
        <div className="rail__name">Nex<em>u</em>s</div>
      </Link>

      <button className="btn" style={{ width: "100%", justifyContent: "flex-start" }}
              onClick={() => router.push("/chat")}>
        <Icon name="plus" size={14} />
        <span>New chat</span>
      </button>

      <div className="rail__section">
        {main.map(m => (
          <div key={m.id}
               className={`nav-item ${page === m.id ? "is-active" : ""}`}
               onClick={() => router.push(m.path)}>
            <Icon name={m.icon} size={15} />
            <span className="truncate">{m.label}</span>
          </div>
        ))}
      </div>

      <div className="rail__scroll">
        {/* Projects, each holding its own chats. A project is a memory scope,
            so this nesting is the storage model, not decoration: a chat inside
            one can recall what the others in it said. */}
        <div className="rail__label">
          Projects
          <span className="add" title="New project" onClick={() => router.push("/projects")}>
            <Icon name="plus" size={12} />
          </span>
        </div>

        {projects.length === 0 && <div className="rail__empty">No projects yet</div>}

        <div className="tree">
          {projects.map(project => {
            const isOpen = expanded.includes(project.id);
            const projectChats = byProject.get(project.id) ?? [];
            return (
              <div key={project.id} className="tree__group">
                <div className={`nav-item ${current?.id === project.id ? "is-current" : ""}`}
                     onClick={() => openProject(project.id)}>
                  <button className={`tree__twist ${isOpen ? "is-open" : ""}`}
                          type="button"
                          aria-label={isOpen ? "Collapse" : "Expand"}
                          aria-expanded={isOpen}
                          onClick={e => { e.stopPropagation(); toggle(project.id); }}>
                    <Icon name="chev" size={12} />
                  </button>
                  <span className="dot" style={{
                    width: 6, height: 6, borderRadius: 999,
                    background: `var(--d-${project.domain})`, flex: "0 0 6px",
                  }} />
                  <span className="truncate">{project.name}</span>
                  <button className="tree__add"
                          type="button"
                          title={`New chat in ${project.name}`}
                          onClick={e => {
                            e.stopPropagation();
                            select(project.id);
                            router.push(`/chat?project=${project.id}`);
                          }}>
                    <Icon name="plus" size={12} />
                  </button>
                </div>

                {isOpen && (
                  <div className="tree__children">
                    {projectChats.length === 0 && (
                      <div className="tree__empty">No chats yet</div>
                    )}
                    {projectChats.map(chat => (
                      <div key={chat.id}
                           className={`nav-item nav-item--leaf ${
                             pathname === `/c/${chat.id}` ? "is-active" : ""
                           }`}
                           onClick={() => router.push(`/c/${chat.id}`)}>
                        <span className="tree__bullet" />
                        <span className="truncate">{chat.title}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Everything filed under no project. These read and write the user
            tier alone, which is what a passing question should touch. */}
        <div className="rail__label" style={{ marginTop: 18 }}>Chats</div>
        {loose.length === 0 && <div className="rail__empty">No loose chats</div>}
        {loose.map(chat => (
          <div key={chat.id}
               className={`nav-item nav-item--leaf ${
                 pathname === `/c/${chat.id}` ? "is-active" : ""
               }`}
               onClick={() => router.push(`/c/${chat.id}`)}>
            <span className="tree__bullet" />
            <span className="truncate">{chat.title}</span>
          </div>
        ))}
      </div>

      <div className="rail__user">
        <div className="rail__avatar">N</div>
        <div className="rail__user-meta">
          <strong>Nexus Local</strong>
          <span>nexus.local · admin</span>
        </div>
      </div>
    </aside>
  );
};

export const TopBar = () => {
  const pathname = usePathname();
  const router = useRouter();

  const page = getPageId(pathname);

  const labels: Record<string, string[]> = {
    chat:         ["Chat", "New conversation"],
    c:            ["Chat", "Conversation"],
    projects:     ["Projects"],
    graph:        ["Knowledge graph"],
    settings:     ["Settings"],
  };
  const trail = labels[page] || [page];

  return (
    <div className="topbar">
      <div className="crumbs">
        {trail.map((t, i) => (
          <React.Fragment key={i}>
            {i > 0 && <span className="sep">/</span>}
            {i === trail.length - 1
              ? <strong>{t}</strong>
              : <span style={{cursor:"pointer"}} onClick={() => router.push("/chat")}>{t}</span>}
          </React.Fragment>
        ))}
      </div>
      <div className="topbar__spacer" />
      <div className="tag tag--accent" title="Local processing only">
        <span className="dot" style={{background:"var(--accent)"}}/>
        ON-DEVICE
      </div>
      <div className="topbar__action" title="Search"><Icon name="search" size={16}/></div>
      <div className="topbar__action" title="Notifications"><Icon name="bell" size={16}/></div>
    </div>
  );
};
