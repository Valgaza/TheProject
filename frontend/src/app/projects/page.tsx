"use client";

/**
 * Real projects, from the core service.
 *
 * Was a hardcoded array of one, with a "New project" modal whose fields had no
 * value or onChange and whose two buttons both just closed it.
 */

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/icons";
import { DOMAINS, domainById } from "@/lib/domains";
import { useProjects } from "@/lib/project-context";

export default function Projects() {
  const router = useRouter();
  const { projects, current, loading, select, create } = useProjects();

  const [filter, setFilter] = useState("all");
  const [modal, setModal] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [newDom, setNewDom] = useState("general");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const filtered = filter === "all" ? projects : projects.filter(p => p.domain === filter);

  const open = (projectId: string) => {
    select(projectId);
    router.push("/chat");
  };

  const submit = async () => {
    if (!name.trim() || saving) return;
    setSaving(true);
    setError(null);
    try {
      await create({
        name: name.trim(),
        description: description.trim() || undefined,
        domain: newDom,
      });
      setModal(false);
      setName("");
      setDescription("");
      router.push("/chat");
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
        {filtered.map(p => {
          const domain = domainById(p.domain);
          return (
            <div key={p.id} className="pcard"
                 style={{ "--d": `var(--d-${domain.id})` } as React.CSSProperties}
                 onClick={() => open(p.id)}>
              <span className="pcard__rule"/>
              <div className="pcard__head">
                <div className="tag" style={{
                  color: `var(--d-${domain.id})`,
                  borderColor: `oklch(from var(--d-${domain.id}) 0.85 0.025 h)`,
                }}>
                  <span className="dot"/>
                  {domain.id}
                </div>
                <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                  {current?.id === p.id ? "● current" : "↗ open"}
                </span>
              </div>
              <h3 className="pcard__name">{p.name}</h3>
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
