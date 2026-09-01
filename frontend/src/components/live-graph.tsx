"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "@/components/icons";
import {
  type ApiGraph,
  type GraphView,
  type MaintenanceResult,
  graphSocketUrl,
  graphUrl,
  runMaintenance,
} from "@/lib/api";
import { SweepReportPanel } from "@/components/sweep-report";

export const NODE_TYPES: Record<string, { label: string; color: string; fill: string }> = {
  entity:  { label: "Entity",   color: "var(--accent)",        fill: "oklch(0.94 0.03 195)" },
  doc:     { label: "Document", color: "oklch(0.55 0.06 240)", fill: "oklch(0.94 0.03 240)" },
  person:  { label: "Person",   color: "oklch(0.55 0.07 30)",  fill: "oklch(0.94 0.03 30)" },
  org:     { label: "Org",      color: "oklch(0.50 0.06 145)", fill: "oklch(0.94 0.03 145)" },
  concept: { label: "Concept",  color: "oklch(0.50 0.06 300)", fill: "oklch(0.94 0.03 300)" },
  metric:  { label: "Metric",   color: "oklch(0.55 0.06 75)",  fill: "oklch(0.94 0.03 75)" },
};

const nt = (type: string) => NODE_TYPES[type.toLowerCase()] ?? NODE_TYPES.entity;

/** The group id says its own tier: "u_<hex>" or "p_<hex>_<hex>". */
const tierOf = (groupId: string): Tier => (groupId.startsWith("p_") ? "project" : "user");

type Tier = "user" | "project";

type GNode = {
  id: string; label: string; type: string; summary: string;
  /** Which memory tier this node lives in, read off its group id. */
  tier: Tier;
  x: number; y: number; r: number;
};
type GEdge = { s: string; t: string; label: string };


const CX = 550, CY = 360;
const RINGS = [
  { count: 1,  radius: 0 },
  { count: 6,  radius: 155 },
  { count: 12, radius: 290 },
  { count: 18, radius: 410 },
];

function positionAt(index: number): { x: number; y: number; r: number } {
  let rem = index;
  for (const ring of RINGS) {
    if (rem < ring.count) {
      const angle = ring.radius === 0 ? 0 : (2 * Math.PI * rem) / ring.count - Math.PI / 2;
      return {
        x: ring.radius === 0 ? CX : Math.round(CX + ring.radius * Math.cos(angle)),
        y: ring.radius === 0 ? CY : Math.round(CY + ring.radius * Math.sin(angle)),
        r: index === 0 ? 10 : 6,
      };
    }
    rem -= ring.count;
  }
  const angle = (2 * Math.PI * index) / 24 - Math.PI / 2;
  return { x: Math.round(CX + 480 * Math.cos(angle)), y: Math.round(CY + 300 * Math.sin(angle)), r: 5 };
}

interface LiveGraphProps {
  className?: string;
  /**
   * Which tier to draw. "user" is the universal graph alone, "project" is one
   * project alone, and "all" is the union — what a retrieval in that project
   * would actually see.
   */
  view?: GraphView;
  /** Required by the "project" view, and by "all" to have a project half. */
  projectId?: string;
}

export function LiveGraph({ className, view = "all", projectId }: LiveGraphProps) {
  const [hover,       setHover]      = useState<string | null>(null);
  const [pinned,      setPinned]     = useState<string | null>(null);
  const [pulse,       setPulse]      = useState<string | null>(null);
  const [graphNodes,  setGraphNodes] = useState<GNode[]>([]);
  const [graphEdges,  setGraphEdges] = useState<GEdge[]>([]);
  const [lastUpdated, setLastUpdated] = useState<string>("–");
  const [loading,     setLoading]    = useState(true);
  const [wsStatus,    setWsStatus]   = useState<"connecting" | "connected" | "disconnected">("connecting");
  const [sweep,       setSweep]      = useState<MaintenanceResult | null>(null);
  const [sweeping,    setSweeping]   = useState(false);

  const svgRef       = useRef<SVGSVGElement>(null);
  const positionsRef = useRef<Map<string, { x: number; y: number; r: number }>>(new Map());
  const nodesRef     = useRef<GNode[]>([]);

  const applyPayload = useCallback((data: ApiGraph) => {
    const nodes: GNode[] = data.nodes.map(n => {
      if (!positionsRef.current.has(n.id)) {
        positionsRef.current.set(n.id, positionAt(positionsRef.current.size));
      }
      return {
        id: n.id, label: n.name, type: n.type, summary: n.summary,
        tier: tierOf(n.group_id),
        ...positionsRef.current.get(n.id)!,
      };
    });
    const edges: GEdge[] = data.edges.map(e => ({ s: e.source, t: e.target, label: e.label }));
    nodesRef.current = nodes;
    setGraphNodes(nodes);
    setGraphEdges(edges);
    setLastUpdated(new Date().toLocaleTimeString());
    setLoading(false);
  }, []);

  useEffect(() => {
    let cancelled = false;

    fetch(graphUrl(view, projectId))
      .then(r => r.json())
      .then((data: ApiGraph) => { if (!cancelled) applyPayload(data); })
      .catch(err => {
        console.error("[graph] fetch failed:", err);
        if (!cancelled) setLoading(false);
      });

    const ws = new WebSocket(graphSocketUrl(view, projectId));
    ws.onopen    = ()  => { if (!cancelled) setWsStatus("connected"); };
    ws.onmessage = e   => { if (!cancelled) applyPayload(JSON.parse(e.data)); };
    ws.onerror   = err => { if (!cancelled) console.error("[graph] WS error:", err); };
    ws.onclose   = ()  => { if (!cancelled) setWsStatus("disconnected"); };

    return () => {
      cancelled = true;
      ws.close();
    };
  }, [applyPayload, view, projectId]);

  useEffect(() => {
    let i = 0;
    const t = setInterval(() => {
      const nodes = nodesRef.current;
      if (nodes.length === 0) return;
      setPulse(nodes[i % nodes.length].id);
      i++;
      setTimeout(() => setPulse(null), 1400);
    }, 4200);
    return () => clearInterval(t);
  }, []);

  const handleReset = async () => {
    // Scoped to what is on screen, so clearing one project no longer takes the
    // universal tier with it — but that is only obvious if the prompt says so.
    const what =
      view === "user" ? "your universal memory"
      : view === "project" ? "this project's memory"
      : "this project's memory and your universal memory";
    if (!window.confirm(`Permanently delete ${what}? This cannot be undone.`)) return;
    try {
      await fetch(graphUrl(view, projectId), { method: "DELETE" });
    } catch (err) {
      console.error("[graph] reset failed:", err);
    }
    setGraphNodes([]);
    setGraphEdges([]);
    nodesRef.current = [];
    positionsRef.current.clear();
    setPinned(null);
    setLastUpdated("–");
  };

  // Dry run first, always. The pass marks rather than deletes so it is
  // reversible, but a diff you have read beats one you have to undo.
  const tidy = async (dryRun: boolean) => {
    setSweeping(true);
    try {
      setSweep(await runMaintenance(view, projectId, dryRun));
    } catch (err) {
      console.error("[graph] maintenance failed:", err);
    } finally {
      setSweeping(false);
    }
  };

  const sel = pinned || hover;
  const focusNode = graphNodes.find(n => n.id === sel);
  const connectedIds = new Set<string | null>([sel]);
  graphEdges.forEach(e => {
    if (e.s === sel) connectedIds.add(e.t);
    if (e.t === sel) connectedIds.add(e.s);
  });
  const nodeById = (id: string) => graphNodes.find(n => n.id === id);

  const wsColor = wsStatus === "connected"
    ? "oklch(0.5 0.08 145)"
    : wsStatus === "disconnected"
    ? "oklch(0.55 0.1 30)"
    : "var(--ink-3)";
  const wsLabel = wsStatus === "connected" ? "live" : wsStatus === "disconnected" ? "offline" : "connecting";

  return (
    <div className={className ?? "graph__stage"}>
      <div className="graph__canvas">
        <svg ref={svgRef} viewBox="0 0 1100 720" preserveAspectRatio="xMidYMid meet">
          <defs>
            <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
              <path d="M 40 0 L 0 0 0 40" fill="none" stroke="oklch(0.9 0.01 75)" strokeWidth="0.5"/>
            </pattern>
            <pattern id="grid-major" width="200" height="200" patternUnits="userSpaceOnUse">
              <path d="M 200 0 L 0 0 0 200" fill="none" stroke="oklch(0.85 0.012 75)" strokeWidth="0.6"/>
            </pattern>
            <radialGradient id="halo" cx="50%" cy="50%" r="50%">
              <stop offset="0%"  stopColor="oklch(0.42 0.06 195 / 0.18)"/>
              <stop offset="100%" stopColor="oklch(0.42 0.06 195 / 0)"/>
            </radialGradient>
          </defs>
          <rect x="0" y="0" width="1100" height="720" fill="url(#grid)"/>
          <rect x="0" y="0" width="1100" height="720" fill="url(#grid-major)"/>

          {loading && (
            <text x="550" y="360" textAnchor="middle" dominantBaseline="middle"
                  fontSize="13" fontFamily="var(--font-mono)" fill="var(--ink-3)"
                  letterSpacing="0.12em">
              LOADING…
            </text>
          )}

          {!loading && graphNodes.length === 0 && (
            <text x="550" y="360" textAnchor="middle" dominantBaseline="middle"
                  fontSize="14" fontFamily="var(--font-sans)" fill="var(--ink-3)">
              {view === "project"
                ? "Nothing in this project yet — talk to it and memory forms."
                : "Graph is empty — send a message to start building memory."}
            </text>
          )}

          <g>
            {graphEdges.map((e, i) => {
              const a = nodeById(e.s), b = nodeById(e.t);
              if (!a || !b) return null;
              const isFocus = sel && (e.s === sel || e.t === sel);
              const dim = sel && !isFocus;
              return (
                <g key={i}>
                  <line
                    x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                    stroke={isFocus ? "var(--accent)" : "oklch(0.6 0.012 75)"}
                    strokeWidth={isFocus ? 1.4 : 0.7}
                    opacity={dim ? 0.08 : (isFocus ? 0.85 : 0.35)}
                    style={{ transition: "opacity 320ms, stroke 320ms, stroke-width 320ms" }}
                  />
                  {isFocus && (
                    <text
                      x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 4}
                      fontSize="9.5" fontFamily="var(--font-mono)" fontWeight="500"
                      fill="var(--accent-deep)" textAnchor="middle"
                      style={{ letterSpacing: "0.08em", textTransform: "uppercase", paintOrder: "stroke", stroke: "var(--bg)", strokeWidth: 4 }}>
                      {e.label}
                    </text>
                  )}
                </g>
              );
            })}
          </g>

          {focusNode && (
            <circle cx={focusNode.x} cy={focusNode.y} r="48" fill="url(#halo)"/>
          )}

          <g>
            {graphNodes.map(n => {
              const t = nt(n.type);
              const isFocus  = sel === n.id;
              const isConn   = sel && connectedIds.has(n.id);
              const dim      = sel && !isConn && !isFocus;
              const isPulsing = pulse === n.id;
              return (
                <g key={n.id}
                   onMouseEnter={() => setHover(n.id)}
                   onMouseLeave={() => setHover(null)}
                   onClick={() => setPinned(n.id)}
                   style={{ cursor: "pointer", opacity: dim ? 0.22 : 1, transition: "opacity 320ms" }}>
                  {isPulsing && (
                    <circle cx={n.x} cy={n.y} r={n.r}
                            fill="none" stroke="var(--accent)" strokeWidth="1"
                            style={{ animation: "ringOut 1.4s ease-out forwards", transformOrigin: `${n.x}px ${n.y}px` }}/>
                  )}
                  {/* Only in the union view: a user-tier node wears a ring so
                      the two tiers can be told apart at a glance. In a
                      single-tier view every node is the same tier, and the
                      ring would be noise. */}
                  {view === "all" && n.tier === "user" && (
                    <circle cx={n.x} cy={n.y} r={n.r + 3.5}
                            fill="none" stroke={t.color} strokeWidth="0.8"
                            strokeDasharray="2 2" opacity="0.7"/>
                  )}
                  <circle cx={n.x} cy={n.y} r={n.r}
                          fill={t.fill} stroke={t.color}
                          strokeWidth={isFocus ? 2 : 1}
                          style={{ transition: "stroke-width 200ms" }}/>
                  {isFocus && (
                    <circle cx={n.x} cy={n.y} r={n.r + 4}
                            fill="none" stroke={t.color} strokeWidth="0.8" opacity="0.5"/>
                  )}
                  <text x={n.x + n.r + 6} y={n.y + 3.5}
                        fontSize="10.5" fontFamily="var(--font-sans)"
                        fontWeight={isFocus ? 600 : 400}
                        fill={isFocus ? "var(--ink)" : "var(--ink-2)"}
                        letterSpacing="-0.005em"
                        style={{ paintOrder: "stroke", stroke: "var(--bg)", strokeWidth: 3, transition: "fill 200ms" }}>
                    {n.label}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>

        <style>{`
          @keyframes ringOut {
            0%   { r: 6;  opacity: 0.9; }
            100% { r: 28; opacity: 0;   }
          }
        `}</style>

        <div className="graph__chrome graph__chrome--tl">
          <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", letterSpacing: "0.12em" }}>
            {view === "user" ? "UNIVERSAL" : view === "project" ? "PROJECT" : "UNIVERSAL + PROJECT"}
          </div>
          <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", letterSpacing: "0.12em" }}>{graphNodes.length} NODES · {graphEdges.length} EDGES</div>
        </div>
        <div className="graph__chrome graph__chrome--tr">
          <button className="ctool"
                  title="Tidy this graph — collapse duplicates and rebuild summaries"
                  disabled={sweeping}
                  onClick={() => void tidy(true)}>
            <Icon name="spark" size={15}/>
          </button>
          <button className="ctool" title="Reset graph" onClick={handleReset}><Icon name="close" size={15}/></button>
        </div>
        <div className="graph__chrome graph__chrome--br">
          <span className="mono" style={{ fontSize: 10.5, color: wsColor }}>● {wsLabel} · {lastUpdated}</span>
        </div>
      </div>

      <aside className="inspector">
        {sweep ? (
          <SweepReportPanel
            result={sweep}
            busy={sweeping}
            onApply={() => void tidy(false)}
            onDismiss={() => setSweep(null)}
          />
        ) : (
        <>
        <div className="inspector__head">
          <div className="eyebrow">§ Inspector</div>
          {focusNode && <button className="ctool" onClick={() => setPinned(null)}><Icon name="close" size={14}/></button>}
        </div>
        {focusNode ? (
          <>
            <div className="inspector__type" style={{ color: nt(focusNode.type).color }}>
              <span className="dot" style={{ background: nt(focusNode.type).color }}/>
              {nt(focusNode.type).label}
            </div>
            <h3 className="inspector__name display">{focusNode.label}</h3>

            {focusNode.summary && (
              <p style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.55, margin: "10px 0 0" }}>
                {focusNode.summary}
              </p>
            )}

            <dl className="inspector__props">
              <div><dt>Tier</dt><dd>{focusNode.tier === "user" ? "Universal" : "Project"}</dd></div>
              <div><dt>ID</dt><dd className="mono">nx://entity/{focusNode.id.slice(0, 8)}</dd></div>
              <div><dt>Connections</dt><dd>{connectedIds.size - 1}</dd></div>
              <div><dt>Last updated</dt><dd>{lastUpdated}</dd></div>
            </dl>

            <div className="eyebrow" style={{ marginTop: 24, marginBottom: 12 }}>§ Connections ({connectedIds.size - 1})</div>
            <div className="inspector__rels">
              {graphEdges
                .filter(e => e.s === sel || e.t === sel)
                .slice(0, 8)
                .map((e, i) => {
                  const other = nodeById(e.s === sel ? e.t : e.s);
                  if (!other) return null;
                  return (
                    <div key={i} className="rel" onClick={() => setPinned(other.id)}>
                      <span className="rel__verb mono">{e.label}</span>
                      <span className="rel__name">{other.label}</span>
                      <span className="rel__type" style={{ color: nt(other.type).color }}>● {nt(other.type).label}</span>
                    </div>
                  );
                })}
            </div>
          </>
        ) : (
          <div style={{ color: "var(--ink-3)", fontSize: 13.5, padding: "24px 0", lineHeight: 1.55 }}>
            <div className="display" style={{ fontSize: 22, color: "var(--ink-2)", letterSpacing: "-0.012em", marginBottom: 12 }}>
              {loading ? "Loading…" : graphNodes.length === 0 ? "Graph is empty." : "Pick a node."}
            </div>
            {!loading && (graphNodes.length === 0
              ? "Send a message in the conversation page to start building the knowledge graph."
              : "Click any node on the canvas to inspect its provenance, properties, and incoming relationships. New nodes pulse as they arrive.")}
          </div>
        )}
        </>
        )}
      </aside>
    </div>
  );
}
