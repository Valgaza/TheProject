"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Icon } from "./icons";
import { CHATS_CHANGED, type Chat, listChats } from "@/lib/api";
import { useProjects } from "@/lib/project-context";

/** "/c/abc" -> "c", "/" -> "landing". Only the first segment names a page. */
const getPageId = (path: string) => {
  const [segment] = path.split("/").filter(Boolean);
  return segment ?? "landing";
};

function relativeTime(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

export const RailNav = () => {
  const pathname = usePathname();
  const router = useRouter();
  const { projects, current, select } = useProjects();
  const [switcherOpen, setSwitcherOpen] = useState(false);

  const page = getPageId(pathname);

  const main = [
    { id: "demo",     label: "Live demo",       icon: "bolt",   path: "/demo" },
    { id: "chat",     label: "New chat",         icon: "chat",   path: "/chat" },
    { id: "projects", label: "Projects",         icon: "folder", count: 1, path: "/projects" },
    { id: "graph",    label: "Knowledge graph",  icon: "graph",  path: "/graph" },
    { id: "rag-demo", label: "RAG Demo",         icon: "flask",  path: "/rag-demo" },
    { id: "settings", label: "Settings",         icon: "cog",    path: "/settings" },
  ];

  // Chats belong to the current project, so the list changes when you switch.
  const [recent, setRecent] = useState<Chat[]>([]);
  const projectId = current?.id;
  useEffect(() => {
    let cancelled = false;
    if (!projectId) {
      setRecent([]);
      return;
    }
    const load = async () => {
      try {
        const chats = await listChats(projectId);
        if (!cancelled) setRecent(chats);
      } catch {
        if (!cancelled) setRecent([]);
      }
    };
    void load();
    window.addEventListener(CHATS_CHANGED, load);
    return () => {
      cancelled = true;
      window.removeEventListener(CHATS_CHANGED, load);
    };
  }, [pathname, projectId]);

  return (
    <aside className="rail">
      <Link href="/" className="rail__brand" style={{ textDecoration: "none", color: "inherit" }}>
        <div className="rail__mark">N</div>
        <div className="rail__name">Nex<em>u</em>s</div>
      </Link>

      <button className="btn" style={{ width: "100%", justifyContent: "flex-start" }}
              onClick={() => router.push("/chat")}>
        <Icon name="plus" size={14} />
        <span>New conversation</span>
        <span style={{ marginLeft: "auto", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)"}}>⌘K</span>
      </button>

      <div className="rail__section">
        {main.map(m => (
          <div key={m.id}
               className={`nav-item ${page === m.id ? "is-active" : ""}`}
               onClick={() => router.push(m.path)}>
            <Icon name={m.icon} size={15} />
            <span className="truncate">{m.label}</span>
            {m.count != null && <span className="count">{m.count}</span>}
          </div>
        ))}
      </div>

      {/* The project is a memory scope, not just a folder: switching changes
          which chats are listed and what the assistant can recall. */}
      <div className="rail__section pswitch">
        <div className="rail__label">
          Project
          <span className="add" onClick={() => router.push("/projects")}>
            <Icon name="plus" size={12}/>
          </span>
        </div>
        {/* A button, not a div: this is a real control, and the rest of the
            rail being click-handlers on divs is not a reason to add another. */}
        <button className="nav-item"
                type="button"
                aria-expanded={switcherOpen}
                aria-label="Switch project"
                style={{ width: "100%", font: "inherit", textAlign: "left" }}
                onClick={() => setSwitcherOpen(o => !o)}>
          <span className="dot" style={{
            width: 6, height: 6, borderRadius: 999,
            background: `var(--d-${current?.domain ?? "general"})`, flex: "0 0 6px",
          }}/>
          <span className="truncate">{current?.name ?? "No project"}</span>
          <span className="count">{switcherOpen ? "▴" : "▾"}</span>
        </button>
        {switcherOpen && (
          <div className="pswitch__menu">
            {projects.map(p => (
              <button key={p.id}
                      className={`pswitch__item ${p.id === current?.id ? "is-on" : ""}`}
                      onClick={() => { select(p.id); setSwitcherOpen(false); }}>
                <span className="dot" style={{
                  width: 6, height: 6, borderRadius: 999,
                  background: `var(--d-${p.domain})`, flex: "0 0 6px",
                }}/>
                <span className="truncate">{p.name}</span>
              </button>
            ))}
            <button className="pswitch__item"
                    onClick={() => { setSwitcherOpen(false); router.push("/projects"); }}>
              <Icon name="plus" size={12}/>
              <span>New project…</span>
            </button>
          </div>
        )}
      </div>

      <div className="rail__section" style={{ flex: 1, minHeight: 0 }}>
        <div className="rail__label">Recent</div>
        <div className="rail__history">
          {recent.length === 0 && (
            <div className="nav-item" style={{ color: "var(--ink-3)", fontSize: 12.5 }}>
              No conversations yet
            </div>
          )}
          {recent.map(chat => (
            <div key={chat.id}
                 className={`nav-item ${pathname === `/c/${chat.id}` ? "is-active" : ""}`}
                 onClick={() => router.push(`/c/${chat.id}`)}>
              <span className="dot" style={{
                width: 5, height: 5, borderRadius: 999,
                background: "var(--d-general)", flex: "0 0 5px",
                opacity: 0.8
              }}/>
              <span className="truncate" style={{ fontSize: 13 }}>{chat.title}</span>
              <span className="count" style={{ fontSize: 10.5 }}>{relativeTime(chat.updated_at)}</span>
            </div>
          ))}
        </div>
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
    demo:         ["Demo"],
    chat:         ["Chat", "New conversation"],
    c:            ["Chat", "Conversation"],
    projects:     ["Projects"],
    graph:        ["Knowledge graph"],
    "rag-demo":   ["RAG Demo", "Knowledge Retrieval Playground"],
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
