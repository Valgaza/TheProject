"use client";

import React, { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/icons";

export const DOMAINS = [
  { id: "research",    name: "Research",    icon: "flask",  blurb: "Lit review, synthesis, cited briefs" },
  { id: "engineering", name: "Engineering", icon: "code",   blurb: "Code review, postmortems, design docs" },
  { id: "legal",       name: "Legal",       icon: "scale",  blurb: "Redlines, clause search, risk maps" },
  { id: "finance",     name: "Finance",     icon: "coin",   blurb: "Models, variance, board narrative" },
  { id: "operations",  name: "Operations",  icon: "truck",  blurb: "Playbooks, SOPs, incident response" },
  { id: "sales",       name: "Sales",       icon: "spark",  blurb: "Outbound, discovery, forecasting" },
];

export const SUGGESTED: Record<string, string[]> = {
  research:    ["Summarise the last 12 months of fuel-cell patents", "Build a literature map of small-language-model papers", "Compare three positions on the EU AI Act"],
  engineering: ["Diagnose the auth-svc latency spike since Tuesday", "Draft an RFC for migrating to columnar storage", "Review this PR for race conditions"],
  legal:       ["Redline the MSA against our standard playbook", "Find every clause referencing data residency", "Draft a one-pager on the new privacy regulation"],
  finance:     ["Reconcile Q3 pipeline cost variance for the EU region", "Build the board narrative for the April update", "Stress-test next-year ARR under three churn scenarios"],
  operations:  ["Write the runbook for a Tier-1 outage", "Audit the new-hire onboarding playbook", "Schedule the field-ops rotation for May"],
  sales:       ["Draft a 3-touch outbound for the Helios persona", "Score this call for MEDDPICC coverage", "Forecast EMEA close-rate by quarter"],
};

const DomainCard = ({ d, active, onSelect, tilt }: any) => {
  const ref = useRef<HTMLButtonElement>(null);
  const [hover, setHover] = useState(false);
  const [pos, setPos] = useState({ x: 0.5, y: 0.5 });

  const onMove = (e: any) => {
    if (!tilt || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    setPos({ x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height });
  };
  const transform = hover && tilt
    ? `perspective(900px) rotateX(${(0.5 - pos.y) * 5}deg) rotateY(${(pos.x - 0.5) * 6}deg) translateY(-3px)`
    : `perspective(900px) rotateX(0) rotateY(0) translateY(0)`;

  return (
    <button
      ref={ref}
      className={`dom ${active ? "is-active" : ""}`}
      onClick={() => onSelect(d.id)}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onMouseMove={onMove}
      style={{ transform, "--d": `var(--d-${d.id})` } as any}>
      <div className="dom__inner">
        <div className="dom__head">
          <span className="dom__num mono">P/{String(DOMAINS.findIndex(x=>x.id===d.id)+1).padStart(2,"0")}</span>
          <span className="dom__icon"><Icon name={d.icon} size={18}/></span>
        </div>
        <div className="dom__body">
          <h3 className="dom__name display">{d.name}</h3>
          <p className="dom__blurb">{d.blurb}</p>
        </div>
        <div className="dom__foot">
          <span className="dom__pick mono">{active ? "● selected" : "↳ select"}</span>
        </div>
        <div className="dom__shimmer" style={{
          background: hover
            ? `radial-gradient(180px circle at ${pos.x*100}% ${pos.y*100}%, oklch(1 0 0 / 0.7), transparent 70%)`
            : "transparent"
        }}/>
      </div>
    </button>
  );
};

export default function ChatHome() {
  const router = useRouter();
  const [domain, setDomain] = useState("research");
  const [val, setVal] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const submit = () => { if (val.trim()) { router.push("/conversation"); } };

  return (
    <div className="chathome">
      <div className="chathome__hero">
        <div className="eyebrow">§ Command</div>
        <h1 className="display chathome__title">
          Good afternoon, Aditya.<br/>
          <em>What</em> are we making?
        </h1>
        <p className="chathome__sub">
          Pick a pipeline, or just start typing — Nexus will route the prompt to the right specialist on its own.
        </p>
      </div>

      <div className="chathome__domains">
        <div className="chathome__strip">
          <span className="eyebrow">§ Pipeline</span>
          <span className="hr-line"/>
          <span className="mono" style={{fontSize:11, color:"var(--ink-3)", letterSpacing:"0.1em"}}>auto-route by default</span>
        </div>
        <div className="domains">
          {DOMAINS.map(d => (
            <DomainCard key={d.id} d={d}
                        active={domain === d.id}
                        onSelect={setDomain}
                        tilt={true}/>
          ))}
        </div>
      </div>

      <div className="chathome__suggest">
        <div className="chathome__strip">
          <span className="eyebrow">§ Suggested</span>
          <span className="hr-line"/>
        </div>
        <div className="chips">
          {SUGGESTED[domain].map((s, i) => (
            <button key={i} className="chip" onClick={() => { setVal(s); inputRef.current?.focus(); }}>
              <span className="chip__num mono">{String(i+1).padStart(2,"0")}</span>
              <span className="chip__text">{s}</span>
              <span className="chip__arrow"><Icon name="arrow" size={14}/></span>
            </button>
          ))}
        </div>
      </div>

      <div className="composer-wrap">
        <div className="composer">
          <div className="composer__top">
            <div className="tag tag--accent">
              <span className="dot" style={{background: `var(--d-${domain})`}}/>
              {DOMAINS.find(d=>d.id===domain)?.name} pipeline
            </div>
            <span className="composer__hint mono">⌘ + ⏎ to send · ⌘K to switch pipeline</span>
          </div>
          <textarea
            ref={inputRef}
            className="composer__input"
            placeholder="Ask Nexus something — or paste a document, link, or table…"
            value={val}
            onChange={e => setVal(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(); }}
            rows={3}
          />
          <div className="composer__bot">
            <div className="composer__tools">
              <button className="ctool" title="Attach"><Icon name="paper" size={16}/></button>
              <button className="ctool" title="Voice"><Icon name="mic" size={16}/></button>
              <button className="ctool" title="Knowledge"><Icon name="book" size={16}/></button>
              <span className="composer__sep"/>
              <span className="mono" style={{fontSize: 11, color:"var(--ink-3)"}}>3 sources · 1.4M docs indexed</span>
            </div>
            <button className="btn btn--primary" onClick={submit}>
              Send <Icon name="arrow" size={14}/>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
