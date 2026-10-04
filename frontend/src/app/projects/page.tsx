"use client";

/**
 * Real projects, from the core service.
 *
 * Was a hardcoded array of one, with a "New project" modal whose fields had no
 * value or onChange and whose two buttons both just closed it.
 */

import React, { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/icons";
import { DOMAINS, domainById } from "@/lib/domains";
import { type Project } from "@/lib/api";
import { useProjects } from "@/lib/project-context";

/** A project card's "..." dropdown -- rename, pin, delete. Same shape and
 * same CSS classes as the chat row's menu (components/shell.tsx); no
 * "move to project" here, a project isn't inside anything to move
 * between. */
function ProjectMenu({
  project,
  onRename,
  onTogglePin,
  onDelete,
  onClose,
}: {
  project: Project;
  onRename: () => void;
  onTogglePin: () => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDocMouseDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [onClose]);

  return (
    <div className="chatmenu chatmenu--card" ref={ref} onClick={e => e.stopPropagation()}>
      {!confirming ? (
        <>
          <button className="chatmenu__item" onClick={() => { onRename(); onClose(); }}>
            <Icon name="pencil" size={14} /> Rename
          </button>
          <button className="chatmenu__item" onClick={() => { onTogglePin(); onClose(); }}>
            <Icon name={project.pinned ? "pinOff" : "pin"} size={14} />
            {project.pinned ? "Unpin" : "Pin"} project
          </button>
          <div className="chatmenu__sep" />
          <button className="chatmenu__item chatmenu__item--danger" onClick={() => setConfirming(true)}>
            <Icon name="trash" size={14} /> Delete
          </button>
        </>
      ) : (
        <div className="chatmenu__confirm">
          <p>
            Delete <strong>{project.name}</strong>? Every chat inside it is deleted too.
            This can&apos;t be undone.
          </p>
          <div className="chatmenu__confirm-actions">
            <button className="btn" type="button" onClick={() => setConfirming(false)}>
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

export default function Projects() {
  const router = useRouter();
  const { projects, current, loading, select, create, update, remove } = useProjects();

  const [filter, setFilter] = useState("all");
  const [modal, setModal] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [newDom, setNewDom] = useState("general");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const filtered = filter === "all" ? projects : projects.filter(p => p.domain === filter);
  // Pinned first, recency (already the order `projects` arrives in)
  // otherwise -- a stable sort, so pinning doesn't reshuffle anything
  // beyond moving pinned cards to the front as a group.
  const sorted = [...filtered].sort((a, b) => Number(b.pinned) - Number(a.pinned));

  const open = (projectId: string) => {
    select(projectId);
    // ?project=<id> is what tells /chat to file the next chat you start
    // under this project instead of leaving it unfiled -- see
    // app/chat/page.tsx. select() above just keeps the sidebar's switcher
    // and "Recent" filter in sync with where you're headed.
    router.push(`/chat?project=${projectId}`);
  };

  const commitRename = async (projectId: string) => {
    const trimmed = renameValue.trim();
    setRenamingId(null);
    if (!trimmed) return;
    try {
      await update(projectId, { name: trimmed });
    } catch {
      // The card just keeps its old name; nothing else depends on this
      // succeeding immediately.
    }
  };

  const submit = async () => {
    if (!name.trim() || saving) return;
    setSaving(true);
    setError(null);
    try {
      const project = await create({
        name: name.trim(),
        description: description.trim() || undefined,
        domain: newDom,
      });
      setModal(false);
      setName("");
      setDescription("");
      // You just named this project on purpose -- the chat you're about
      // to start belongs in it, not floating unfiled.
      router.push(`/chat?project=${project.id}`);
    } catch {
      setError("Couldn't create the project. Is the core service running on :8100?");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="projects">
      <div className="projects__head">
        <div>
          <div className="eyebrow">§ Workspace</div>
          <h1 className="projects__title">Projects.</h1>
        </div>
        <button className="btn btn--primary btn--lg" onClick={() => setModal(true)}>
          <Icon name="plus" size={14}/> New project
        </button>
      </div>

      <div className="projects__filters">
        {[{ id: "all", l: "All" }, ...DOMAINS.map(d => ({ id: d.id, l: d.name }))].map(f => (
          <button key={f.id}
                  className={`pf ${filter === f.id ? "is-active" : ""}`}
                  onClick={() => setFilter(f.id)}>
            {f.l}
            <span className="count">
              {f.id === "all" ? projects.length : projects.filter(p => p.domain === f.id).length}
            </span>
          </button>
        ))}
      </div>

      <div className="pgrid">
        {sorted.map(p => {
          const domain = domainById(p.domain);
          const renaming = renamingId === p.id;
          return (
            <div key={p.id} className="pcard"
                 style={{ "--d": `var(--d-${domain.id})` } as React.CSSProperties}
                 onClick={() => !renaming && open(p.id)}>
              <span className="pcard__rule"/>
              <div className="pcard__head">
                <div className="tag" style={{
                  color: `var(--d-${domain.id})`,
                  borderColor: `oklch(from var(--d-${domain.id}) 0.85 0.025 h)`,
                }}>
                  <span className="dot"/>
                  {domain.id}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, position: "relative" }}>
                  {p.pinned && <Icon name="pin" size={13} style={{ color: "var(--accent)" }} />}
                  <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                    {current?.id === p.id ? "● current" : "↗ open"}
                  </span>
                  <button
                    type="button"
                    className="chat-row__more"
                    style={{ display: "flex" }}
                    aria-label="Project options"
                    onClick={e => { e.stopPropagation(); setMenuOpenId(o => (o === p.id ? null : p.id)); }}
                  >
                    <Icon name="dots" size={14} />
                  </button>
                  {menuOpenId === p.id && (
                    <ProjectMenu
                      project={p}
                      onRename={() => { setRenameValue(p.name); setRenamingId(p.id); }}
                      onTogglePin={() => { void update(p.id, { pinned: !p.pinned }); }}
                      onDelete={() => {
                        void remove(p.id).then(() => {
                          if (current?.id === p.id) router.push("/chat");
                        });
                      }}
                      onClose={() => setMenuOpenId(null)}
                    />
                  )}
                </div>
              </div>
              {renaming ? (
                <input
                  className="input"
                  style={{ marginBottom: 8, fontSize: 22 }}
                  value={renameValue}
                  onClick={e => e.stopPropagation()}
                  onChange={e => setRenameValue(e.target.value)}
                  onBlur={() => void commitRename(p.id)}
                  onKeyDown={e => {
                    if (e.key === "Enter") { e.preventDefault(); void commitRename(p.id); }
                    if (e.key === "Escape") setRenamingId(null);
                  }}
                  autoFocus
                />
              ) : (
                <h3 className="pcard__name">{p.name}</h3>
              )}
              <p className="pcard__desc">
                {p.description || "No description yet."}
              </p>
              <div className="pcard__meta">
                <span>created {new Date(p.created_at).toLocaleDateString()}</span>
                <span style={{ marginLeft: "auto" }}>
                  upd. {new Date(p.updated_at).toLocaleDateString()}
                </span>
              </div>
            </div>
          );
        })}

        {!loading && filtered.length === 0 && (
          <div className="empty-card" style={{ gridColumn: "span 3" }} onClick={() => setModal(true)}>
            <div className="empty-card__icon"><Icon name="folder" size={20}/></div>
            <div className="empty-card__t">
              {projects.length === 0 ? "No projects yet" : "Nothing in this pipeline"}
            </div>
            <div className="empty-card__s">Create one to start grouping conversations and memory.</div>
          </div>
        )}

        {!loading && filtered.length > 0 && (
          <div className="empty-card" onClick={() => setModal(true)}>
            <div className="empty-card__icon"><Icon name="plus" size={18}/></div>
            <div className="empty-card__t">Start a new project</div>
            <div className="empty-card__s">A project is its own slice of memory.</div>
          </div>
        )}
      </div>

      {modal && (
        <div className="modal-back" onClick={() => setModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="eyebrow">§ New project</div>
            <h2 className="modal__t">Name your work.</h2>
            <p className="modal__s">
              A project bundles conversations and its own slice of the knowledge graph.
              What you tell Nexus here stays here, unless it is true of you everywhere.
            </p>
            <div className="field">
              <label className="field__l">Project name</label>
              <input className="input"
                     placeholder="e.g. Technical Knowledge Base"
                     value={name}
                     onChange={e => setName(e.target.value)}
                     onKeyDown={e => { if (e.key === "Enter") void submit(); }}
                     autoFocus/>
            </div>
            <div className="field">
              <label className="field__l">Default pipeline</label>
              <div className="dpicks">
                {DOMAINS.map(d => (
                  <button key={d.id}
                          className={`dpick ${newDom === d.id ? "is-on" : ""}`}
                          onClick={() => setNewDom(d.id)}>
                    <span className="dot" style={{ background: `var(--d-${d.id})` }}/>
                    {d.name}
                  </button>
                ))}
              </div>
            </div>
            <div className="field">
              <label className="field__l">Description (optional)</label>
              <input className="input"
                     placeholder="One line of context…"
                     value={description}
                     onChange={e => setDescription(e.target.value)}/>
            </div>
            {error && (
              <p className="mono" style={{ fontSize: 12, color: "var(--ink-3)" }}>{error}</p>
            )}
            <div className="modal__bot">
              <button className="btn btn--ghost" onClick={() => setModal(false)}>Cancel</button>
              <button className="btn btn--primary"
                      onClick={submit}
                      disabled={saving || !name.trim()}>
                {saving ? "Creating…" : <>Create project <Icon name="arrow" size={14}/></>}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
