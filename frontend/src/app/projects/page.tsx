"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/icons";
import { DOMAINS } from "@/app/chat/page";

const PROJECTS = [
  { id: 1, name: "Atlas Forecast", desc: "Rolling 18-month financial model, EU pipeline cost reconciliation.", domain: "finance",     conv: 47, members: 4, updated: "2h",   spark: [3,4,2,5,6,4,7,8,6,9,8,11] },
  { id: 2, name: "Helios Migration", desc: "Cutover of legacy auth-svc to columnar storage. Postmortems, RFCs.", domain: "engineering", conv: 124, members: 9, updated: "1d", spark: [2,3,3,5,7,6,8,7,9,11,10,12] },
  { id: 3, name: "Vendor Compliance", desc: "Quarterly redline cycle against the master playbook.", domain: "legal", conv: 33, members: 3, updated: "3d", spark: [1,2,3,2,3,4,3,5,4,4,5,6] },
  { id: 4, name: "Field Ops 2026", desc: "Onboarding, dispatch, and incident playbooks for the regional teams.", domain: "operations", conv: 58, members: 6, updated: "Mon", spark: [4,5,3,6,5,7,6,8,7,9,8,10] },
  { id: 5, name: "Outbound Atlas", desc: "Persona-based outbound and discovery scoring across EMEA + NA.", domain: "sales", conv: 76, members: 5, updated: "Apr 18", spark: [2,2,3,4,3,5,4,6,5,7,6,8] },
  { id: 6, name: "Patent Landscape", desc: "Lit review of fuel-cell IP since 2022, weekly digest.", domain: "research", conv: 19, members: 2, updated: "Apr 15", spark: [1,1,2,2,3,2,4,3,4,5,4,5] },
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
  const [newDom, setNewDom] = useState("research");

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
          { id: "all", l: "All", c: PROJECTS.length },
          { id: "research", l: "Research", c: PROJECTS.filter(p=>p.domain==="research").length },
          { id: "engineering", l: "Engineering", c: PROJECTS.filter(p=>p.domain==="engineering").length },
          { id: "legal", l: "Legal", c: PROJECTS.filter(p=>p.domain==="legal").length },
          { id: "finance", l: "Finance", c: PROJECTS.filter(p=>p.domain==="finance").length },
          { id: "operations", l: "Operations", c: PROJECTS.filter(p=>p.domain==="operations").length },
          { id: "sales", l: "Sales", c: PROJECTS.filter(p=>p.domain==="sales").length },
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
              <span className="mono" style={{fontSize:11, color: "var(--ink-3)"}}>↗ open</span>
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
              <input className="input" placeholder="e.g. Q4 Fundraising Narrative" autoFocus/>
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
