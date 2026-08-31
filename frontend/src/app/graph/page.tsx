"use client";

/**
 * The knowledge graph, one tier at a time.
 *
 * There are two kinds of memory and they answer different questions. The
 * universal graph is what stays true about you everywhere; a project graph is
 * what is only true of one body of work. Drawing them as a single cloud —
 * which is what this page used to do — makes it impossible to see which tier a
 * fact actually landed in, and that is the thing most worth checking.
 */

import React, { useEffect, useState } from "react";
import { LiveGraph, NODE_TYPES } from "@/components/live-graph";
import { type GraphView } from "@/lib/api";
import { useProjects } from "@/lib/project-context";

/** "user" plus one entry per project. Encoded so it survives a select value. */
type Selection = { view: GraphView; projectId?: string };

export default function Graph() {
  const { projects, current } = useProjects();
  const [selection, setSelection] = useState<Selection>({ view: "user" });
  const [withUniversal, setWithUniversal] = useState(false);

  // Land on whatever project the rail says you are in, if any.
  useEffect(() => {
    if (current) setSelection({ view: "project", projectId: current.id });
  }, [current?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const isProject = selection.view === "project";
  // "Include universal" is the union — exactly what a retrieval in this
  // project would see, as opposed to what this project alone remembers.
  const effectiveView: GraphView = isProject && withUniversal ? "all" : selection.view;

  const project = projects.find(p => p.id === selection.projectId);
  const subtitle = !isProject
    ? "what stays true about you, across every project"
    : withUniversal
    ? `${project?.name ?? "this project"} plus everything universal — what a question here can recall`
    : `${project?.name ?? "this project"} alone — nothing from any other project, nothing universal`;

  return (
    <div className="graph">
      <div className="graph__head">
        <div>
          <div className="eyebrow">§ Knowledge graph</div>
          <h1 className="graph__title">Living memory of every conversation.</h1>
          <p className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 6 }}>
            {subtitle}
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

      <div className="gviews">
        <button className={`gview ${!isProject ? "is-on" : ""}`}
                onClick={() => setSelection({ view: "user" })}>
          <span className="gview__tier">universal</span>
          <span className="gview__name">You</span>
        </button>

        {projects.map(p => (
          <button key={p.id}
                  className={`gview ${selection.projectId === p.id && isProject ? "is-on" : ""}`}
                  onClick={() => setSelection({ view: "project", projectId: p.id })}>
            <span className="gview__tier" style={{ color: `var(--d-${p.domain})` }}>project</span>
            <span className="gview__name">{p.name}</span>
          </button>
        ))}

        {projects.length === 0 && (
          <span className="gviews__note">No projects yet — only the universal tier exists.</span>
        )}

        {isProject && (
          <label className="gtoggle">
            <input type="checkbox"
                   checked={withUniversal}
                   onChange={e => setWithUniversal(e.target.checked)} />
            include universal
          </label>
        )}
      </div>

      {/* Remounted per view, so positions and the socket are rebuilt rather
          than a project's nodes being laid out in the user tier's slots. */}
      <LiveGraph key={`${effectiveView}:${selection.projectId ?? ""}`}
                 view={effectiveView}
                 projectId={selection.projectId} />
    </div>
  );
}
