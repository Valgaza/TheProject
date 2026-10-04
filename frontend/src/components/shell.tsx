"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Icon } from "./icons";
import {
  CHATS_CHANGED,
  type Chat,
  type Project,
  deleteChat,
  listAllChats,
  logout as apiLogout,
  notifyChatsChanged,
  updateChat,
} from "@/lib/api";
import { useProjects } from "@/lib/project-context";
import { useAuth } from "@/lib/auth-context";

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

/** "3m", "5h", "2d" -- the timestamp on a chat row, which has room for
 * about four characters and no more. */
function relativeTime(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

type MenuView = "main" | "move" | "delete-confirm";

/** The dropdown behind a chat row's "..." button -- rename, pin, move to
 * project, delete. One panel whose contents swap between three views
 * (rather than nested flyouts) so it stays one absolutely-positioned
 * element, same trick the project switcher (.pswitch__menu) already uses. */
function ChatMenu({
  chat,
  projects,
  onRename,
  onTogglePin,
  onMove,
  onDelete,
  onClose,
}: {
  chat: Chat;
  projects: Project[];
  onRename: () => void;
  onTogglePin: () => void;
  onMove: (projectId: string) => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const [view, setView] = useState<MenuView>("main");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDocMouseDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [onClose]);

  const otherProjects = projects.filter(p => p.id !== chat.project_id);

  return (
    <div className="chatmenu" ref={ref} onClick={e => e.stopPropagation()}>
      {view === "main" && (
        <>
          <button className="chatmenu__item" onClick={() => { onRename(); onClose(); }}>
            <Icon name="pencil" size={14} /> Rename
          </button>
          <button className="chatmenu__item" onClick={() => { onTogglePin(); onClose(); }}>
            <Icon name={chat.pinned ? "pinOff" : "pin"} size={14} />
            {chat.pinned ? "Unpin" : "Pin"} chat
          </button>
          <button
            className="chatmenu__item"
            onClick={() => setView("move")}
            disabled={otherProjects.length === 0}
          >
            <Icon name="folder" size={14} /> Move to project
          </button>
          <div className="chatmenu__sep" />
          <button
            className="chatmenu__item chatmenu__item--danger"
            onClick={() => setView("delete-confirm")}
          >
            <Icon name="trash" size={14} /> Delete
          </button>
        </>
      )}

      {view === "move" && (
        <>
          <button className="chatmenu__item chatmenu__back" onClick={() => setView("main")}>
            <Icon name="chev" size={14} style={{ transform: "rotate(180deg)" }} /> Back
          </button>
          <div className="chatmenu__sep" />
          {otherProjects.map(p => (
            <button
              key={p.id}
              className="chatmenu__item"
              onClick={() => { onMove(p.id); onClose(); }}
            >
              <span
                className="dot"
                style={{ width: 6, height: 6, borderRadius: 999, background: `var(--d-${p.domain})` }}
              />
              <span className="truncate">{p.name}</span>
            </button>
          ))}
        </>
      )}

      {view === "delete-confirm" && (
        <div className="chatmenu__confirm">
          <p>Delete this chat? This can&apos;t be undone.</p>
          <div className="chatmenu__confirm-actions">
            <button className="btn" type="button" onClick={() => setView("main")}>
              Cancel
            </button>
            <button
              className="btn chatmenu__item--danger"
              type="button"
              onClick={() => { onDelete(); onClose(); }}
            >
              <Icon name="trash" size={13} /> Delete
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function ChatRow({
  chat,
  active,
  projects,
  onNavigate,
  onChanged,
  onDeleted,
}: {
  chat: Chat;
  active: boolean;
  projects: Project[];
  onNavigate: () => void;
  /** Rename, pin/unpin, move -- the chat still exists, just refetch. */
  onChanged: () => void;
  /** Delete specifically -- the chat is gone, not just changed; the
   * caller needs to know in case it's the one currently open. */
  onDeleted: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  // Only ever read while renaming (the display span below always renders
  // chat.title directly) -- so it only needs to be (re-)seeded with the
  // current title at the moment rename mode starts, not kept in sync via
  // an effect the rest of the time.
  const [title, setTitle] = useState(chat.title);
  const renameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (renaming) renameRef.current?.select();
  }, [renaming]);

  const commitRename = async () => {
    setRenaming(false);
    const trimmed = title.trim();
    if (!trimmed || trimmed === chat.title) {
      setTitle(chat.title);
      return;
    }
    try {
      await updateChat(chat.id, { title: trimmed });
      onChanged();
    } catch {
      setTitle(chat.title);
    }
  };

  return (
    <div
      className={`nav-item chat-row ${active ? "is-active" : ""}`}
      onClick={() => !renaming && onNavigate()}
    >
      {chat.pinned && <Icon name="pin" size={11} className="chat-row__pin" />}
      <span
        className="dot"
        style={{ width: 5, height: 5, borderRadius: 999, background: "var(--d-general)", flex: "0 0 5px", opacity: 0.8 }}
      />
      {renaming ? (
        <input
          ref={renameRef}
          className="chat-row__rename"
          value={title}
          onClick={e => e.stopPropagation()}
          onChange={e => setTitle(e.target.value)}
          onBlur={commitRename}
          onKeyDown={e => {
            if (e.key === "Enter") { e.preventDefault(); void commitRename(); }
            if (e.key === "Escape") { setTitle(chat.title); setRenaming(false); }
          }}
          autoFocus
        />
      ) : (
        <span className="truncate" style={{ fontSize: 13 }}>{chat.title}</span>
      )}
      <span className="count chat-row__time" style={{ fontSize: 10.5 }}>
        {relativeTime(chat.updated_at)}
      </span>
      <button
        type="button"
        className={`chat-row__more ${menuOpen ? "is-open" : ""}`}
        aria-label="Chat options"
        onClick={e => { e.stopPropagation(); setMenuOpen(o => !o); }}
      >
        <Icon name="dots" size={14} />
      </button>
      {menuOpen && (
        <ChatMenu
          chat={chat}
          projects={projects}
          onRename={() => { setTitle(chat.title); setRenaming(true); }}
          onTogglePin={() => {
            void updateChat(chat.id, { pinned: !chat.pinned }).then(onChanged);
          }}
          onMove={projectId => {
            void updateChat(chat.id, { project_id: projectId }).then(onChanged);
          }}
          onDelete={() => {
            void deleteChat(chat.id).then(onDeleted);
          }}
          onClose={() => setMenuOpen(false)}
        />
      )}
    </div>
  );
}

export const RailNav = () => {
  const pathname = usePathname();
  const router = useRouter();
  const { projects, current, select } = useProjects();
  const { user, refresh } = useAuth();

  const handleSignOut = async () => {
    try {
      await apiLogout();
    } finally {
      // RequireAuth (app-shell.tsx) sends the user to /login the moment
      // useAuth's `user` goes null -- no manual router.push needed here.
      await refresh();
    }
  };

  const page = getPageId(pathname);

  const main = [
    { id: "projects",  label: "Projects",        icon: "folder", count: projects.length || undefined, path: "/projects" },
    { id: "documents", label: "Documents",       icon: "paper",  path: "/documents" },
    { id: "graph",     label: "Knowledge graph", icon: "graph",  path: "/graph" },
    { id: "settings",  label: "Settings",        icon: "cog",    path: "/settings" },
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

  const pinnedChats = useMemo(() => chats.filter(c => c.pinned), [chats]);

  const handleChatDeleted = (chatId: string) => {
    // The one case that can leave the user looking at a page for a chat
    // that no longer exists -- send them back to a fresh one instead of
    // leaving a 404'd conversation on screen.
    if (pathname === `/c/${chatId}`) router.push("/chat");
    notifyChatsChanged();
  };

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

        {/* Pinned chats, as a shortcut list. A pinned chat still lives in its
            project below -- this is a jump list, not a second home for it. */}
        {pinnedChats.length > 0 && (
          <div className="rail__history" style={{ marginBottom: 12 }}>
            {pinnedChats.map(chat => (
              <ChatRow
                key={chat.id}
                chat={chat}
                active={pathname === `/c/${chat.id}`}
                projects={projects}
                onNavigate={() => router.push(`/c/${chat.id}`)}
                onChanged={notifyChatsChanged}
                onDeleted={() => handleChatDeleted(chat.id)}
              />
            ))}
          </div>
        )}

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
                      <ChatRow
                        key={chat.id}
                        chat={chat}
                        active={pathname === `/c/${chat.id}`}
                        projects={projects}
                        onNavigate={() => router.push(`/c/${chat.id}`)}
                        onChanged={notifyChatsChanged}
                        onDeleted={() => handleChatDeleted(chat.id)}
                      />
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
          <ChatRow
            key={chat.id}
            chat={chat}
            active={pathname === `/c/${chat.id}`}
            projects={projects}
            onNavigate={() => router.push(`/c/${chat.id}`)}
            onChanged={notifyChatsChanged}
            onDeleted={() => handleChatDeleted(chat.id)}
          />
        ))}
      </div>

      <div className="rail__user">
        <div className="rail__avatar">{(user?.display_name ?? "?").charAt(0).toUpperCase()}</div>
        <div className="rail__user-meta">
          <strong className="truncate">{user?.display_name ?? "…"}</strong>
          <span className="truncate">{user?.email ?? ""}</span>
        </div>
        <button
          type="button"
          className="rail__signout"
          title="Sign out"
          aria-label="Sign out"
          onClick={handleSignOut}
        >
          <Icon name="close" size={13} />
        </button>
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
