"use client";

import React from "react";
import { LiveGraph, NODE_TYPES } from "@/components/live-graph";
import { useProjects } from "@/lib/project-context";

export default function Graph() {
  const { current } = useProjects();

  return (
    <div className="graph">
      <div className="graph__head">
        <div>
          <div className="eyebrow">§ Knowledge graph</div>
          <h1 className="graph__title">Living memory of every conversation.</h1>
          <p className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 6 }}>
            {current
              ? `what you know, plus ${current.name}`
              : "what you know across every project"}
          </p>
        </div>
        <div className="graph__legend">
          {Object.entries(NODE_TYPES).map(([k, v]) => (
            <span key={k} className="leg">
              <span className="leg__sw" style={{ background: v.fill, borderColor: v.color }}/>
              {v.label}
            </span>
          ))}
        </div>
      </div>
      <LiveGraph projectId={current?.id} />
    </div>
  );
}
