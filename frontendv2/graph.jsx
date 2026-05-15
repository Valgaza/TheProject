/* Nexus — Knowledge graph */

const NODE_TYPES = {
  entity:     { label: "Entity",     color: "var(--accent)",       fill: "oklch(0.94 0.03 195)" },
  doc:        { label: "Document",   color: "oklch(0.55 0.06 240)", fill: "oklch(0.94 0.03 240)" },
  person:     { label: "Person",     color: "oklch(0.55 0.07 30)",  fill: "oklch(0.94 0.03 30)" },
  org:        { label: "Org",        color: "oklch(0.50 0.06 145)", fill: "oklch(0.94 0.03 145)" },
  concept:    { label: "Concept",    color: "oklch(0.50 0.06 300)", fill: "oklch(0.94 0.03 300)" },
  metric:     { label: "Metric",     color: "oklch(0.55 0.06 75)",  fill: "oklch(0.94 0.03 75)" },
};

// Manually placed graph — clusters
const GRAPH = (() => {
  const nodes = [];
  const edges = [];
  const add = (id, label, type, x, y, r) => nodes.push({ id, label, type, x, y, r: r || 6 });
  const link = (s, t, label) => edges.push({ s, t, label });

  // CENTER cluster — Q3 board narrative
  add("q3","Q3 Board Narrative","entity", 600, 360, 11);
  add("eu","EU Variance","metric",        430, 320, 8);
  add("frankfurt","Frankfurt-2","org",    310, 260, 7);
  add("ledger","Ledger.csv","doc",        330, 380, 6);
  add("rev","Net Revenue $48.2M","metric",550, 240, 7);
  add("aditya","Aditya K.","person",      720, 270, 6);
  add("board","Board Memo Q2","doc",      720, 450, 6);
  add("margin","Gross Margin","metric",   640, 460, 6);
  add("apac","APAC growth","metric",      790, 380, 6);

  // LEFT — engineering cluster
  add("authsvc","auth-svc","entity",      170, 480, 9);
  add("incident","INC-2046","entity",     250, 560, 7);
  add("kafka","kafka-prod","org",         100, 580, 6);
  add("rfc","RFC-128","doc",              90,  410, 6);
  add("postgres","Postgres 16","org",     200, 380, 6);
  add("rama","Rama D.","person",          90,  500, 5);

  // TOP — research cluster
  add("fc","Fuel-cell IP","concept",      460, 130, 8);
  add("paper1","Toyota '24 patent","doc", 330, 100, 6);
  add("paper2","MIT '25 paper","doc",     560, 80,  6);
  add("paper3","Bosch '25 brief","doc",   620, 150, 5);
  add("trend","weekly digest","entity",   430, 200, 6);

  // RIGHT — legal cluster
  add("msa","MSA Vendor X","entity",      890, 240, 9);
  add("clause","Data residency","concept",990, 180, 7);
  add("redline","Redline v3","doc",       980, 320, 6);
  add("counsel","Asha P.","person",       870, 150, 5);
  add("playbook","Master Playbook","doc", 990, 410, 6);

  // BOTTOM — operations cluster
  add("playook2","Tier-1 Runbook","doc",  470, 600, 6);
  add("rotation","May Rotation","entity", 580, 640, 6);
  add("regions","EMEA region","concept",  690, 600, 6);
  add("ops1","Ops Lead","person",         400, 660, 5);

  // BOTTOM-RIGHT — sales
  add("helios","Helios persona","entity", 870, 580, 8);
  add("seq","Outbound v3","doc",          940, 640, 6);
  add("call","Discovery call","entity",   790, 640, 6);

  // floating pings
  add("plat","Platform v2.4","concept",    240, 200, 5);
  add("model","llama-3.1-70b","concept",  150, 320, 5);
  add("idx","Index shard","concept",      770, 510, 5);

  // edges — relationships
  link("q3","eu","cites"); link("q3","rev","reports"); link("q3","margin","includes");
  link("q3","aditya","authored by"); link("q3","board","supersedes"); link("q3","apac","includes");
  link("eu","frankfurt","operates"); link("eu","ledger","sourced from"); link("frankfurt","ledger","feeds");
  link("rev","margin","drives");
  link("authsvc","incident","caused"); link("authsvc","postgres","uses");
  link("authsvc","kafka","emits to"); link("authsvc","rfc","subject of");
  link("rfc","rama","authored by"); link("incident","rama","assigned");
  link("trend","fc","tracks"); link("trend","paper1","includes");
  link("trend","paper2","includes"); link("trend","paper3","includes");
  link("paper2","fc","cites");
  link("msa","clause","contains"); link("msa","redline","modifies");
  link("redline","counsel","reviewed"); link("msa","playbook","aligned with");
  link("playook2","rotation","ref"); link("playook2","regions","applies to"); link("rotation","ops1","owned by");
  link("helios","seq","drives"); link("helios","call","exemplified");
  link("model","authsvc","powers"); link("model","q3","powers");
  link("plat","model","ships"); link("idx","apac","feeds");
  link("eu","margin","affects"); link("aditya","board","authored");
  link("postgres","ledger","stores");

  return { nodes, edges };
})();

const Graph = () => {
  const [hover, setHover] = React.useState(null);
  const [pinned, setPinned] = React.useState("q3");
  const [pulse, setPulse] = React.useState(null);
  const svgRef = React.useRef(null);

  // periodic "new node" pulse
  React.useEffect(() => {
    const ids = ["paper3","seq","idx","redline","apac"];
    let i = 0;
    const t = setInterval(() => {
      setPulse(ids[i % ids.length]);
      i++;
      setTimeout(() => setPulse(null), 1400);
    }, 4200);
    return () => clearInterval(t);
  }, []);

  const sel = pinned || hover;
  const focusNode = GRAPH.nodes.find(n => n.id === sel);
  const connectedIds = new Set([sel]);
  GRAPH.edges.forEach(e => {
    if (e.s === sel) connectedIds.add(e.t);
    if (e.t === sel) connectedIds.add(e.s);
  });

  const nodeById = id => GRAPH.nodes.find(n => n.id === id);

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

      <div className="graph__stage">
        <div className="graph__canvas">
          <svg ref={svgRef} viewBox="0 0 1100 720" preserveAspectRatio="xMidYMid meet">
            {/* gridded backdrop */}
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

            {/* edges */}
            <g>
              {GRAPH.edges.map((e, i) => {
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
                      style={{ transition: "opacity 320ms, stroke 320ms, stroke-width 320ms"}}
                    />
                    {isFocus && (
                      <text
                        x={(a.x + b.x)/2} y={(a.y + b.y)/2 - 4}
                        fontSize="9.5"
                        fontFamily="var(--font-mono)"
                        fontWeight="500"
                        fill="var(--accent-deep)"
                        textAnchor="middle"
                        style={{ letterSpacing: "0.08em", textTransform: "uppercase", paintOrder: "stroke", stroke: "var(--bg)", strokeWidth: 4}}>
                        {e.label}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>

            {/* halo for focus */}
            {focusNode && (
              <circle cx={focusNode.x} cy={focusNode.y} r="48" fill="url(#halo)"/>
            )}

            {/* nodes */}
            <g>
              {GRAPH.nodes.map(n => {
                const t = NODE_TYPES[n.type];
                const isFocus = sel === n.id;
                const isConn = sel && connectedIds.has(n.id);
                const dim = sel && !isConn && !isFocus;
                const isPulsing = pulse === n.id;
                return (
                  <g key={n.id}
                     onMouseEnter={() => setHover(n.id)}
                     onMouseLeave={() => setHover(null)}
                     onClick={() => setPinned(n.id)}
                     style={{ cursor: "pointer", opacity: dim ? 0.22 : 1, transition: "opacity 320ms"}}>
                    {isPulsing && (
                      <circle cx={n.x} cy={n.y} r={n.r}
                              fill="none" stroke="var(--accent)"
                              strokeWidth="1"
                              style={{ animation: "ringOut 1.4s ease-out forwards", transformOrigin: `${n.x}px ${n.y}px`}}/>
                    )}
                    <circle cx={n.x} cy={n.y} r={n.r}
                            fill={t.fill}
                            stroke={t.color}
                            strokeWidth={isFocus ? 2 : 1}
                            style={{ transition: "stroke-width 200ms, r 200ms"}}/>
                    {isFocus && (
                      <circle cx={n.x} cy={n.y} r={n.r + 4}
                              fill="none" stroke={t.color} strokeWidth="0.8" opacity="0.5"/>
                    )}
                    <text x={n.x + n.r + 6} y={n.y + 3.5}
                          fontSize="10.5"
                          fontFamily="var(--font-sans)"
                          fontWeight={isFocus ? 600 : 400}
                          fill={isFocus ? "var(--ink)" : "var(--ink-2)"}
                          letterSpacing="-0.005em"
                          style={{ paintOrder: "stroke", stroke: "var(--bg)", strokeWidth: 3, transition: "fill 200ms"}}>
                      {n.label}
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>

          <style>{`
            @keyframes ringOut {
              0% { r: 6; opacity: 0.9; }
              100% { r: 28; opacity: 0; }
            }
          `}</style>

          {/* canvas chrome */}
          <div className="graph__chrome graph__chrome--tl">
            <div className="mono" style={{fontSize:10.5, color:"var(--ink-3)", letterSpacing:"0.12em"}}>NEXUS · GRAPH</div>
            <div className="mono" style={{fontSize:10.5, color:"var(--ink-3)", letterSpacing:"0.12em"}}>{GRAPH.nodes.length} NODES · {GRAPH.edges.length} EDGES</div>
          </div>
          <div className="graph__chrome graph__chrome--tr">
            <button className="ctool"><Icon name="search" size={15}/></button>
            <button className="ctool"><Icon name="plus" size={15}/></button>
          </div>
          <div className="graph__chrome graph__chrome--br">
            <span className="mono" style={{fontSize:10.5, color:"var(--ink-3)"}}>● live · 47ms ago</span>
          </div>
        </div>

        {/* — Inspector — */}
        <aside className="inspector">
          <div className="inspector__head">
            <div className="eyebrow">§ Inspector</div>
            {focusNode && <button className="ctool" onClick={() => setPinned(null)}><Icon name="close" size={14}/></button>}
          </div>
          {focusNode ? (
            <>
              <div className="inspector__type" style={{ color: NODE_TYPES[focusNode.type].color }}>
                <span className="dot" style={{ background: NODE_TYPES[focusNode.type].color }}/>
                {NODE_TYPES[focusNode.type].label}
              </div>
              <h3 className="inspector__name display">{focusNode.label}</h3>

              <dl className="inspector__props">
                <div><dt>ID</dt><dd className="mono">nx://{focusNode.type}/{focusNode.id}</dd></div>
                <div><dt>Created</dt><dd>2026-04-12 · 14:32 UTC</dd></div>
                <div><dt>Sources</dt><dd>3 documents · 1 thread</dd></div>
                <div><dt>Confidence</dt><dd>
                  <span className="bar"><span className="bar__fill" style={{width:"86%"}}/></span>
                  <span className="mono" style={{marginLeft:8}}>0.86</span>
                </dd></div>
              </dl>

              <div className="eyebrow" style={{marginTop:24, marginBottom:12}}>§ Connections ({connectedIds.size - 1})</div>
              <div className="inspector__rels">
                {GRAPH.edges
                  .filter(e => e.s === sel || e.t === sel)
                  .slice(0, 8)
                  .map((e, i) => {
                    const other = e.s === sel ? nodeById(e.t) : nodeById(e.s);
                    return (
                      <div key={i} className="rel" onClick={() => setPinned(other.id)}>
                        <span className="rel__verb mono">{e.label}</span>
                        <span className="rel__name">{other.label}</span>
                        <span className="rel__type" style={{color: NODE_TYPES[other.type].color}}>● {other.type}</span>
                      </div>
                    );
                  })}
              </div>

              <div className="eyebrow" style={{marginTop:24, marginBottom:12}}>§ Mentioned in</div>
              <div className="rel" style={{padding:"10px 0", borderTop: "1px solid var(--line)"}}>
                <span className="rel__verb mono">thread</span>
                <span className="rel__name">Q3 board narrative draft</span>
                <span className="mono" style={{fontSize:10.5, color:"var(--ink-3)"}}>↗</span>
              </div>
            </>
          ) : (
            <div style={{color:"var(--ink-3)", fontSize:13.5, padding:"24px 0", lineHeight:1.55}}>
              <div className="display" style={{fontSize:22, color:"var(--ink-2)", letterSpacing:"-0.012em", marginBottom: 12}}>Pick a node.</div>
              Click any node on the canvas to inspect its provenance, properties, and incoming relationships. New nodes pulse as they arrive.
            </div>
          )}
        </aside>
      </div>
    </div>
  );
};

window.Graph = Graph;
