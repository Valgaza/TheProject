"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/icons";
import { DOMAINS } from "@/app/chat/page";

const PROJECTS = [
  {
    id: 1,
    name: "Knowledge Base Q&A",
    desc: "Technical documentation retrieval and cited Q&A across the engineering knowledge base.",
    domain: "rag",
    conv: 34,
    members: 3,
    updated: "1h",
    spark: [2, 3, 4, 3, 5, 6, 7, 8, 9, 11, 12, 14],
  },
];

const Sparkline = ({ data, color }: { data: number[], color: string }) => {
  const max = Math.max(...data);
  const w = 200, h = 28;
  const step = w / (data.length - 1);
  const pts = data.map((v, i) => `${(i * step).toFixed(1)},${(h - (v / max) * (h - 4) - 2).toFixed(1)}`).join(" ");
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round"/>
      <polyline points={`0,${h} ${pts} ${w},${h}`} fill={color} opacity="0.08"/>
    </svg>
  );
};

export default function Projects() {
  const router = useRouter();
  const [filter, setFilter] = useState("all");
  const [modal, setModal] = useState(false);
  const [newDom, setNewDom] = useState("rag");

  const filtered = filter === "all" ? PROJECTS : PROJECTS.filter(p => p.domain === filter);

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
        {[
          { id: "all",     l: "All",           c: PROJECTS.length },
          { id: "rag",     l: "RAG Q&A",       c: PROJECTS.filter(p=>p.domain==="rag").length },
          { id: "general", l: "Conversational", c: PROJECTS.filter(p=>p.domain==="general").length },
          { id: "speech",  l: "Speech",         c: PROJECTS.filter(p=>p.domain==="speech").length },
          { id: "vision",  l: "Vision",         c: PROJECTS.filter(p=>p.domain==="vision").length },
        ].map(f => (
          <button key={f.id}
                  className={`pf ${filter === f.id ? "is-active" : ""}`}
                  onClick={() => setFilter(f.id)}>
            {f.l}<span className="count">{f.c}</span>
          </button>
        ))}
      </div>

      <div className="pgrid">
        {filtered.map(p => (
          <div key={p.id} className="pcard"
               style={{ "--d": `var(--d-${p.domain})` } as any}
               onClick={() => router.push("/conversation")}>
            <span className="pcard__rule"/>
            <div className="pcard__head">
              <div className="tag" style={{ color: `var(--d-${p.domain})`, borderColor: `oklch(from var(--d-${p.domain}) 0.85 0.025 h)`}}>
                <span className="dot"/>
                {p.domain}
              </div>
              <span className="mono" style={{fontSize:11, color:"var(--ink-3)"}}>↗ open</span>
            </div>
            <h3 className="pcard__name">{p.name}</h3>
            <p className="pcard__desc">{p.desc}</p>
            <div className="pcard__sparkline">
              <Sparkline data={p.spark} color={`var(--d-${p.domain})`}/>
            </div>
            <div className="pcard__meta">
              <span><strong>{p.conv}</strong> threads</span>
              <span><strong>{p.members}</strong> members</span>
              <span style={{marginLeft:"auto"}}>upd. {p.updated}</span>
            </div>
          </div>
        ))}

        {filtered.length === 0 && (
          <div className="empty-card" style={{ gridColumn: "span 3" }} onClick={() => setModal(true)}>
            <div className="empty-card__icon"><Icon name="folder" size={20}/></div>
            <div className="empty-card__t">No projects in this pipeline yet</div>
            <div className="empty-card__s">Create one to start grouping conversations and sources.</div>
          </div>
        )}

        <div className="empty-card" onClick={() => setModal(true)}>
          <div className="empty-card__icon"><Icon name="plus" size={18}/></div>
          <div className="empty-card__t">Start a new project</div>
          <div className="empty-card__s">Group threads, attach sources, share with a team.</div>
        </div>
      </div>

      {modal && (
        <div className="modal-back" onClick={() => setModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="eyebrow">§ New project</div>
            <h2 className="modal__t">Name your work.</h2>
            <p className="modal__s">A project bundles conversations, attached sources, and a slice of the knowledge graph.</p>
            <div className="field">
              <label className="field__l">Project name</label>
              <input className="input" placeholder="e.g. Technical Knowledge Base" autoFocus/>
            </div>
            <div className="field">
              <label className="field__l">Default pipeline</label>
              <div className="dpicks">
                {DOMAINS.map(d => (
                  <button key={d.id}
                          className={`dpick ${newDom === d.id ? "is-on" : ""}`}
                          onClick={() => setNewDom(d.id)}>
                    <span className="dot" style={{background: `var(--d-${d.id})`}}/>
                    {d.name}
                  </button>
                ))}
              </div>
            </div>
            <div className="field">
              <label className="field__l">Description (optional)</label>
              <input className="input" placeholder="One line of context for collaborators…"/>
            </div>
            <div className="modal__bot">
              <button className="btn btn--ghost" onClick={() => setModal(false)}>Cancel</button>
              <button className="btn btn--primary" onClick={() => setModal(false)}>Create project <Icon name="arrow" size={14}/></button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
