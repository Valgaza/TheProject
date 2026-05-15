/* Nexus — Landing page */

const ParticleField = ({ variant = "particles", intensity = 1 }) => {
  const canvasRef = React.useRef(null);
  const mouseRef = React.useRef({ x: -9999, y: -9999, active: false });
  const rafRef = React.useRef(0);
  const tRef = React.useRef(0);

  React.useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    let dpr = Math.min(window.devicePixelRatio || 1, 2);

    const resize = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      c.width = c.clientWidth * dpr;
      c.height = c.clientHeight * dpr;
    };
    resize();
    window.addEventListener("resize", resize);

    // Build particles based on variant
    let particles = [];
    const W = () => c.clientWidth;
    const H = () => c.clientHeight;

    const seed = () => {
      const N = variant === "mesh" ? 28 : variant === "topo" ? 60 : 110;
      particles = Array.from({ length: N }).map((_, i) => ({
        x: Math.random() * W(),
        y: Math.random() * H(),
        vx: (Math.random() - 0.5) * 0.18,
        vy: (Math.random() - 0.5) * 0.18,
        r: 0.6 + Math.random() * 1.6,
        a: 0.25 + Math.random() * 0.55,
        phase: Math.random() * Math.PI * 2,
      }));
    };
    seed();

    const onMove = (e) => {
      const r = c.getBoundingClientRect();
      mouseRef.current = { x: e.clientX - r.left, y: e.clientY - r.top, active: true };
    };
    const onLeave = () => { mouseRef.current.active = false; };
    c.addEventListener("mousemove", onMove);
    c.addEventListener("mouseleave", onLeave);

    const tick = () => {
      tRef.current += 0.008 * intensity;
      const t = tRef.current;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W(), H());

      const m = mouseRef.current;

      if (variant === "mesh") {
        // soft warping mesh blobs
        const blobs = [
          { x: W()*0.25 + Math.sin(t*0.7)*60, y: H()*0.4 + Math.cos(t*0.6)*40, r: 320, c: "oklch(0.92 0.04 195 / 0.55)"},
          { x: W()*0.7 + Math.cos(t*0.5)*80, y: H()*0.55 + Math.sin(t*0.8)*50, r: 380, c: "oklch(0.95 0.03 80 / 0.5)"},
          { x: W()*0.5 + Math.sin(t*0.9)*100, y: H()*0.3 + Math.cos(t*0.7)*60, r: 280, c: "oklch(0.88 0.05 215 / 0.45)"},
        ];
        if (m.active) blobs.push({ x: m.x, y: m.y, r: 220, c: "oklch(0.9 0.06 200 / 0.55)"});
        for (const b of blobs) {
          const g = ctx.createRadialGradient(b.x, b.y, 0, b.x, b.y, b.r);
          g.addColorStop(0, b.c);
          g.addColorStop(1, "oklch(0.985 0.005 75 / 0)");
          ctx.fillStyle = g;
          ctx.fillRect(0, 0, W(), H());
        }
        rafRef.current = requestAnimationFrame(tick);
        return;
      }

      if (variant === "topo") {
        ctx.lineWidth = 1;
        ctx.strokeStyle = "oklch(0.42 0.06 195 / 0.18)";
        const cols = 80, rows = 50;
        const stepX = W() / cols, stepY = H() / rows;
        for (let r = 1; r < rows; r += 2) {
          ctx.beginPath();
          for (let cIdx = 0; cIdx < cols; cIdx++) {
            const x = cIdx * stepX;
            let y = r * stepY
              + Math.sin(cIdx * 0.18 + t + r * 0.4) * 8
              + Math.sin(cIdx * 0.06 - t * 0.7) * 14;
            if (m.active) {
              const dx = x - m.x, dy = y - m.y;
              const d = Math.sqrt(dx*dx + dy*dy);
              const k = Math.max(0, 180 - d) / 180;
              y -= k * 30;
            }
            cIdx === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
          }
          ctx.globalAlpha = 0.35 + 0.4 * Math.sin(r * 0.3 + t);
          ctx.stroke();
        }
        ctx.globalAlpha = 1;
        rafRef.current = requestAnimationFrame(tick);
        return;
      }

      if (variant === "drift") {
        // geometric drifting forms (lines + circles)
        ctx.strokeStyle = "oklch(0.42 0.06 195 / 0.35)";
        ctx.lineWidth = 1;
        const shapes = 18;
        for (let i = 0; i < shapes; i++) {
          const phase = i * 0.7;
          let x = (W() * 0.5) + Math.cos(t * 0.3 + phase) * (180 + i * 12);
          let y = (H() * 0.5) + Math.sin(t * 0.4 + phase * 1.3) * (120 + i * 8);
          if (m.active) {
            const dx = m.x - x, dy = m.y - y;
            x += dx * 0.04;
            y += dy * 0.04;
          }
          const r = 30 + (i % 5) * 14;
          ctx.beginPath();
          if (i % 3 === 0) ctx.arc(x, y, r, 0, Math.PI * 2);
          else if (i % 3 === 1) {
            ctx.moveTo(x - r, y); ctx.lineTo(x + r, y);
            ctx.moveTo(x, y - r); ctx.lineTo(x, y + r);
          } else {
            ctx.rect(x - r/2, y - r/2, r, r);
          }
          ctx.globalAlpha = 0.18 + 0.4 * Math.abs(Math.sin(t + phase));
          ctx.stroke();
        }
        ctx.globalAlpha = 1;
        rafRef.current = requestAnimationFrame(tick);
        return;
      }

      // — particles (default) —
      // draw connecting links first
      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];
        // physics
        p.x += p.vx;
        p.y += p.vy;
        // breathing wobble
        p.x += Math.sin(t * 0.6 + p.phase) * 0.08;
        p.y += Math.cos(t * 0.5 + p.phase) * 0.08;

        // cursor attractor
        if (m.active) {
          const dx = m.x - p.x, dy = m.y - p.y;
          const d = Math.sqrt(dx*dx + dy*dy);
          if (d < 220) {
            const k = (1 - d/220) * 0.012;
            p.vx += dx * k;
            p.vy += dy * k;
          }
        }
        // damping
        p.vx *= 0.985; p.vy *= 0.985;
        // wrap
        if (p.x < -20) p.x = W() + 20;
        if (p.x > W() + 20) p.x = -20;
        if (p.y < -20) p.y = H() + 20;
        if (p.y > H() + 20) p.y = -20;
      }
      // links
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const a = particles[i], b = particles[j];
          const dx = a.x - b.x, dy = a.y - b.y;
          const d2 = dx*dx + dy*dy;
          if (d2 < 130*130) {
            const alpha = (1 - Math.sqrt(d2)/130) * 0.18;
            ctx.strokeStyle = `oklch(0.42 0.06 195 / ${alpha})`;
            ctx.lineWidth = 0.6;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }
        }
      }
      // dots
      for (const p of particles) {
        ctx.beginPath();
        ctx.fillStyle = `oklch(0.32 0.05 200 / ${p.a})`;
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fill();
      }
      // cursor halo
      if (m.active) {
        const g = ctx.createRadialGradient(m.x, m.y, 0, m.x, m.y, 200);
        g.addColorStop(0, "oklch(0.42 0.06 195 / 0.16)");
        g.addColorStop(1, "oklch(0.42 0.06 195 / 0)");
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, W(), H());
      }

      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(rafRef.current);
      window.removeEventListener("resize", resize);
      c.removeEventListener("mousemove", onMove);
      c.removeEventListener("mouseleave", onLeave);
    };
  }, [variant, intensity]);

  return <canvas ref={canvasRef} className="hero__canvas" />;
};

const Reveal = ({ children, delay = 0, as: Tag = "div", ...props }) => {
  const ref = React.useRef(null);
  const [visible, setVisible] = React.useState(false);
  React.useEffect(() => {
    const el = ref.current; if (!el) return;
    const io = new IntersectionObserver((entries) => {
      entries.forEach(e => {
        if (e.isIntersecting) { setTimeout(() => setVisible(true), delay); io.disconnect(); }
      });
    }, { threshold: 0.18 });
    io.observe(el);
    return () => io.disconnect();
  }, [delay]);
  return <Tag ref={ref} className={`reveal ${visible ? "in" : ""} ${props.className || ""}`} {...props}>{children}</Tag>;
};

const Landing = ({ setPage, hero, intensity }) => {
  const features = [
    { tag: "01 / Local-first", title: "Your data never leaves the building.", body: "All inference, indexing, and retrieval runs on hardware you own. No tokens billed, no payloads logged, no vendor compliance reviews." },
    { tag: "02 / Six pipelines", title: "One interface, the right specialist.", body: "Nexus auto-routes prompts to the pipeline most fluent in the task — Research, Engineering, Legal, Finance, Operations, Sales — so the model behind every answer is the right one." },
    { tag: "03 / Living graph", title: "Every conversation deepens the system.", body: "Entities, sources, and relationships extracted from each session are stitched into a knowledge graph the entire org can interrogate." },
  ];
  const pipelines = [
    { name: "Research", line: "Lit review · synthesis · citations", domain: "research"},
    { name: "Engineering", line: "Code · review · postmortems", domain: "engineering"},
    { name: "Legal", line: "Redlines · clause search · risk", domain: "legal"},
    { name: "Finance", line: "Modelling · variance · narrative", domain: "finance"},
    { name: "Operations", line: "Playbooks · SOPs · incident", domain: "operations"},
    { name: "Sales", line: "Outbound · discovery · forecast", domain: "sales"},
  ];

  return (
    <div className="landing">
      {/* — Top utility bar — */}
      <div className="landing__top">
        <div className="landing__brand">
          <div className="rail__mark" style={{width:30, height:30, fontSize: 17}}>N</div>
          <span className="landing__brand-name">Nex<em>u</em>s</span>
        </div>
        <nav className="landing__nav">
          <a href="#pipelines">Pipelines</a>
          <a href="#how">How it works</a>
          <a href="#system">System</a>
          <a href="#docs">Docs</a>
        </nav>
        <div style={{ display: "flex", gap: 10 }}>
          <button className="btn btn--ghost" onClick={() => setPage("chat")}>Sign in</button>
          <button className="btn btn--primary" onClick={() => setPage("chat")}>
            Open Nexus <Icon name="arrow" size={14}/>
          </button>
        </div>
      </div>

      {/* — Hero — */}
      <section className="hero">
        <ParticleField variant={hero} intensity={intensity}/>
        <div className="hero__inner">
          <div className="hero__eyebrow eyebrow">
            <span className="dot" style={{
              width: 6, height: 6, borderRadius: 999,
              background: "var(--accent)", display: "inline-block", marginRight: 8,
              boxShadow: "0 0 0 3px oklch(0.42 0.06 195 / 0.18)"
            }}/>
            Local-first AI · v2.4 · April 2026
          </div>
          <h1 className="hero__title display">
            The orchestration layer<br/>
            for <em>private</em> enterprise AI.
          </h1>
          <p className="hero__sub">
            Six purpose-tuned pipelines, one chat surface, and a living knowledge graph —
            running entirely on your own infrastructure. No outbound traffic, no tokens billed,
            no compromise on the answer.
          </p>
          <div className="hero__cta">
            <button className="btn btn--primary btn--xl" onClick={() => setPage("chat")}>
              Enter Nexus <Icon name="arrow" size={16}/>
            </button>
            <button className="btn btn--xl" onClick={() => setPage("graph")}>
              See the graph
            </button>
          </div>
          <div className="hero__meta">
            <span><strong>237</strong> orgs running on-prem</span>
            <span className="dot-sep"/>
            <span><strong>1.4M</strong> documents indexed locally</span>
            <span className="dot-sep"/>
            <span><strong>0</strong> bytes egressed</span>
          </div>
        </div>

        {/* corner ornaments */}
        <div className="hero__corner hero__corner--tl">
          <span className="mono">N · 037</span>
          <span className="mono">52.5200° N</span>
        </div>
        <div className="hero__corner hero__corner--tr">
          <span className="mono">REV 2.4.1</span>
          <span className="mono">13.4050° E</span>
        </div>
        <div className="hero__corner hero__corner--bl">
          <span className="mono">↳ scroll</span>
        </div>
        <div className="hero__corner hero__corner--br">
          <span className="mono">on-device · 47ms</span>
        </div>
      </section>

      {/* — Section: features — */}
      <section className="section section--features" id="how">
        <Reveal className="section__head">
          <div className="eyebrow">§ Principles</div>
          <h2 className="display section__title">
            Built around <em>three</em> commitments.
          </h2>
        </Reveal>
        <div className="features">
          {features.map((f, i) => (
            <Reveal className="feature" key={i} delay={i * 80}>
              <div className="feature__tag mono">{f.tag}</div>
              <h3 className="feature__title display">{f.title}</h3>
              <p className="feature__body">{f.body}</p>
              <div className="feature__rule"/>
              <div className="feature__foot mono">↳ Read paper</div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* — Section: pipelines — */}
      <section className="section section--pipelines" id="pipelines">
        <Reveal className="section__head section__head--split">
          <div>
            <div className="eyebrow">§ Pipelines</div>
            <h2 className="display section__title">Six specialists. <em>One</em> surface.</h2>
          </div>
          <p className="section__lede">
            Every prompt is auto-routed to the pipeline that scores highest on
            domain fluency, retrieval relevance, and compliance posture. You can
            override at any time with a single keystroke.
          </p>
        </Reveal>
        <div className="pipelines">
          {pipelines.map((p, i) => (
            <Reveal className="pipe" key={p.name} delay={i * 60}>
              <div className="pipe__row">
                <span className="pipe__num mono">P/{String(i+1).padStart(2,"0")}</span>
                <span className="pipe__dot" style={{background: `var(--d-${p.domain})`}}/>
                <h3 className="pipe__name display">{p.name}</h3>
                <span className="pipe__arrow"><Icon name="arrow" size={16}/></span>
              </div>
              <div className="pipe__line">{p.line}</div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* — Section: how it works — */}
      <section className="section section--how" id="system">
        <Reveal className="section__head">
          <div className="eyebrow">§ Architecture</div>
          <h2 className="display section__title">
            A short tour of <em>what happens</em> after you press send.
          </h2>
        </Reveal>
        <div className="how">
          {[
            { n: "01", k: "Capture", body: "Your prompt is parsed locally. Sensitive entities are tagged and never leave the air-gap." },
            { n: "02", k: "Route", body: "A small router model picks the best of six pipelines based on intent, vocabulary, and provenance signals." },
            { n: "03", k: "Retrieve", body: "Each pipeline pulls from its own embedding index and tool set — code search, contract corpus, ledgers, etc." },
            { n: "04", k: "Compose", body: "The selected model drafts an answer, cites every claim back to the graph, and streams it to your screen." },
            { n: "05", k: "Stitch", body: "New entities and relationships extracted from the answer are merged into the knowledge graph." },
          ].map((s, i) => (
            <Reveal className="step" key={s.n} delay={i * 60}>
              <div className="step__rule"/>
              <div className="step__num mono">{s.n}</div>
              <h4 className="step__k display">{s.k}</h4>
              <p className="step__body">{s.body}</p>
            </Reveal>
          ))}
        </div>
      </section>

      {/* — Section: closing CTA — */}
      <section className="section section--close">
        <Reveal>
          <h2 className="display close__title">
            Premium enterprise AI<br/>
            without the <em>egress</em>.
          </h2>
          <div className="hero__cta" style={{justifyContent:"center"}}>
            <button className="btn btn--primary btn--xl" onClick={() => setPage("chat")}>
              Open Nexus <Icon name="arrow" size={16}/>
            </button>
            <button className="btn btn--xl">Talk to a deploy engineer</button>
          </div>
        </Reveal>
      </section>

      {/* — Footer — */}
      <footer className="foot">
        <div className="foot__top">
          <div className="foot__brand">
            <div className="rail__mark" style={{width:36, height:36, fontSize: 20}}>N</div>
            <div>
              <div className="display" style={{fontSize: 28, lineHeight: 1, letterSpacing: "-0.02em"}}>Nex<em style={{color:"var(--accent)", fontStyle:"italic"}}>u</em>s</div>
              <div className="mono" style={{fontSize: 11, color: "var(--ink-3)", marginTop: 4, letterSpacing: "0.18em", textTransform:"uppercase"}}>Local-first AI orchestration</div>
            </div>
          </div>
          <div className="foot__cols">
            <div>
              <div className="eyebrow">Product</div>
              <a>Pipelines</a><a>Graph</a><a>Projects</a><a>Changelog</a>
            </div>
            <div>
              <div className="eyebrow">Resources</div>
              <a>Documentation</a><a>Architecture paper</a><a>Security</a><a>Status</a>
            </div>
            <div>
              <div className="eyebrow">Company</div>
              <a>About</a><a>Customers</a><a>Careers</a><a>Press</a>
            </div>
          </div>
        </div>
        <div className="foot__rule"/>
        <div className="foot__bot">
          <span className="mono">© 2026 Nexus Systems · Berlin · Bengaluru</span>
          <span className="mono">SOC 2 Type II · ISO 27001 · GDPR · HIPAA</span>
          <span className="mono">v2.4.1 · build 037</span>
        </div>
      </footer>
    </div>
  );
};

window.Landing = Landing;
window.Reveal = Reveal;
