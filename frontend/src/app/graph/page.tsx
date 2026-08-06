"use client";

import React from "react";
import { LiveGraph, NODE_TYPES } from "@/components/live-graph";

export default function Graph() {
  return (
    <div className="graph">
      <div className="graph__head">
        <div>
          <div className="eyebrow">§ Knowledge graph</div>
          <h1 className="graph__title">Living memory of every conversation.</h1>
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
      <LiveGraph />
    </div>
  );
}
