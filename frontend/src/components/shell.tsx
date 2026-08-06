"use client";

import React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Icon } from "./icons";

export const RailNav = () => {
  const pathname = usePathname();
  const router = useRouter();

  const getPageId = (path: string) => {
    if (path === "/") return "landing";
    return path.replace("/", "");
  };
  const page = getPageId(pathname);

  const main = [
    { id: "demo",     label: "Live demo",       icon: "bolt",   path: "/demo" },
    { id: "chat",     label: "New chat",         icon: "chat",   path: "/chat" },
    { id: "projects", label: "Projects",         icon: "folder", count: 1, path: "/projects" },
    { id: "graph",    label: "Knowledge graph",  icon: "graph",  path: "/graph" },
    { id: "settings", label: "Settings",         icon: "cog",    path: "/settings" },
  ];

  const recent = [
    { id: "r1", title: "Vector similarity metrics",    domain: "rag",     time: "2h" },
    { id: "r2", title: "Embedding model comparison",   domain: "rag",     time: "yesterday" },
    { id: "r3", title: "Project status draft",         domain: "general", time: "yesterday" },
    { id: "r4", title: "Team standup transcript",      domain: "speech",  time: "Mon" },
    { id: "r5", title: "Dashboard screenshot review",  domain: "vision",  time: "Mon" },
    { id: "r6", title: "API documentation Q&A",        domain: "rag",     time: "Apr 18" },
  ];

  const projects = [
    { id: "p1", name: "Knowledge Base Q&A", domain: "rag" },
  ];

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

      <div className="rail__section">
        <div className="rail__label">
          Pinned projects <span className="add"><Icon name="plus" size={12}/></span>
        </div>
        {projects.map(p => (
          <div key={p.id} className="nav-item" onClick={() => router.push("/projects")}>
            <span className="dot" style={{
              width: 6, height: 6, borderRadius: 999,
              background: `var(--d-${p.domain})`, flex: "0 0 6px"
            }}/>
            <span className="truncate">{p.name}</span>
          </div>
        ))}
      </div>

      <div className="rail__section" style={{ flex: 1, minHeight: 0 }}>
        <div className="rail__label">Recent</div>
        <div className="rail__history">
          {recent.map(r => (
            <div key={r.id} className="nav-item" onClick={() => router.push("/conversation")}>
              <span className="dot" style={{
                width: 5, height: 5, borderRadius: 999,
                background: `var(--d-${r.domain})`, flex: "0 0 5px",
                opacity: 0.8
              }}/>
              <span className="truncate" style={{ fontSize: 13 }}>{r.title}</span>
              <span className="count" style={{ fontSize: 10.5 }}>{r.time}</span>
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

  const getPageId = (path: string) => {
    if (path === "/") return "landing";
    return path.replace("/", "");
  };
  const page = getPageId(pathname);

  const labels: Record<string, string[]> = {
    demo:         ["Demo"],
    chat:         ["Chat", "New conversation"],
    conversation: ["Chat", "Vector similarity metrics"],
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
