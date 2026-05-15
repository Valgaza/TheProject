"use client";

import React, { useState } from "react";

const Toggle = ({ on, set }: any) => <div className={`toggle ${on ? "is-on" : ""}`} onClick={() => set(!on)}/>;
const Seg = ({ value, set, options }: any) => (
  <div className="seg">
    {options.map((o: any) =>
      <button key={o.v} className={value === o.v ? "is-on" : ""} onClick={() => set(o.v)}>{o.l}</button>
    )}
  </div>
);

export default function Settings() {
  const [lang, setLang] = useState("en");
  const [theme, setTheme] = useState("light");
  const [stream, setStream] = useState(true);
  const [auto, setAuto] = useState(true);
  const [telemetry, setTelemetry] = useState(false);
  const [encryption, setEncryption] = useState(true);
  const [redact, setRedact] = useState(true);
  const [active, setActive] = useState("general");

  return (
    <div className="settings">
      <div className="settings__head">
        <div className="eyebrow">§ Preferences</div>
        <h1 className="settings__title">Settings.</h1>
        <p style={{color:"var(--ink-2)", fontSize:15, margin:0, maxWidth: 580}}>
          Configure how Nexus behaves on this device. Changes are stored locally and sync to your other Nexus instances over your private network.
        </p>
      </div>

      <div className="set-grid">
        <nav className="set-side">
          <div className="eyebrow" style={{marginBottom:12}}>§ Sections</div>
          {[
            { id: "general",   l: "General" },
            { id: "model",     l: "Pipelines & models" },
            { id: "privacy",   l: "Privacy & local data" },
            { id: "appearance",l: "Appearance" },
            { id: "shortcuts", l: "Shortcuts" },
            { id: "team",      l: "Team & roles" },
            { id: "danger",    l: "Danger zone" },
          ].map(s => (
            <a key={s.id}
               className={active === s.id ? "is-on" : ""}
               onClick={() => setActive(s.id)}>
              {s.l}
            </a>
          ))}
        </nav>

        <div>
          <section className="sgroup">
            <h2 className="sgroup__t">General</h2>
            <p className="sgroup__s">Defaults for every new conversation and project.</p>

            <div className="srow">
              <div>
                <div className="srow__l">Default pipeline</div>
                <div className="srow__d">Auto-route uses the router model to pick the right pipeline. You can always override per-conversation.</div>
              </div>
              <Seg value="auto" set={()=>{}} options={[
                { v: "auto", l: "Auto-route" },
                { v: "ask",  l: "Ask me" },
                { v: "research", l: "Research" },
              ]}/>
            </div>

            <div className="srow">
              <div>
                <div className="srow__l">Stream responses</div>
                <div className="srow__d">Show tokens as they arrive. Disable to wait for the full response before rendering.</div>
              </div>
              <Toggle on={stream} set={setStream}/>
            </div>

            <div className="srow">
              <div>
                <div className="srow__l">Suggest follow-ups</div>
                <div className="srow__d">Nexus proposes the next three things you might want to ask.</div>
              </div>
              <Toggle on={auto} set={setAuto}/>
            </div>

            <div className="srow">
              <div>
                <div className="srow__l">Language</div>
                <div className="srow__d">UI language and default response language.</div>
              </div>
              <Seg value={lang} set={setLang} options={[
                { v: "en", l: "English" }, { v: "de", l: "Deutsch" }, { v: "ja", l: "日本語" }
              ]}/>
            </div>
          </section>

          <section className="sgroup">
            <h2 className="sgroup__t">Privacy & local data</h2>
            <p className="sgroup__s">Nexus runs entirely on hardware you control. These are the few moving parts that touch storage.</p>

            <div className="srow">
              <div>
                <div className="srow__l">Encrypt index at rest</div>
                <div className="srow__d">AES-256 encryption of the embedding index and conversation logs. Requires a passphrase on boot.</div>
              </div>
              <Toggle on={encryption} set={setEncryption}/>
            </div>
            <div className="srow">
              <div>
                <div className="srow__l">Redact PII before retrieval</div>
                <div className="srow__d">Scrub names, IDs, and emails from prompts before they reach the retriever. Adds ~80ms latency.</div>
              </div>
              <Toggle on={redact} set={setRedact}/>
            </div>
            <div className="srow">
              <div>
                <div className="srow__l">Anonymous telemetry</div>
                <div className="srow__d">Send aggregated, non-identifying performance data so we can fix what's broken. <strong style={{color:"var(--ink)"}}>Off by default.</strong></div>
              </div>
              <Toggle on={telemetry} set={setTelemetry}/>
            </div>
          </section>

          <section className="sgroup">
            <h2 className="sgroup__t">Appearance</h2>
            <p className="sgroup__s">Aesthetic preferences for this device.</p>

            <div className="srow">
              <div>
                <div className="srow__l">Theme</div>
                <div className="srow__d">Nexus is light-only by design. Dark mode is on the roadmap for late 2026.</div>
              </div>
              <Seg value={theme} set={setTheme} options={[
                { v: "light", l: "Light" }, { v: "auto", l: "Auto", }, { v: "dark", l: "Dark · soon" }
              ]}/>
            </div>

            <div className="srow">
              <div>
                <div className="srow__l">Density</div>
                <div className="srow__d">Adjust spacing and type scale across the interface.</div>
              </div>
              <Seg value="comfortable" set={()=>{}} options={[
                { v: "compact", l: "Compact" }, { v: "comfortable", l: "Comfortable" }, { v: "spacious", l: "Spacious" }
              ]}/>
            </div>

            <div className="srow">
              <div>
                <div className="srow__l">Reduced motion</div>
                <div className="srow__d">Honour the system preference and disable parallax, particle, and slide-in animations.</div>
              </div>
              <Toggle on={false} set={()=>{}}/>
            </div>
          </section>

          <section className="sgroup">
            <h2 className="sgroup__t">Pipelines & models</h2>
            <p className="sgroup__s">Inspect what's installed locally and how it's allocated.</p>

            <div className="surface" style={{padding: 0, marginTop: 8}}>
              {[
                { p: "Research",    m: "llama-3.1-70b-q4", v: "8.4 GB", state: "ready" },
                { p: "Engineering", m: "qwen-2.5-coder-32b", v: "6.1 GB", state: "ready" },
                { p: "Legal",       m: "llama-3.1-70b-q4", v: "8.4 GB", state: "ready" },
                { p: "Finance",     m: "llama-3.1-70b-q4", v: "8.4 GB", state: "ready" },
                { p: "Operations",  m: "phi-4-14b",        v: "3.2 GB", state: "warming" },
                { p: "Sales",       m: "llama-3.1-8b",     v: "2.1 GB", state: "ready" },
              ].map((row, i) => (
                <div key={i} style={{
                  display:"grid",
                  gridTemplateColumns:"160px 1fr 90px 110px 80px",
                  alignItems:"center",
                  padding: "14px 18px",
                  borderTop: i ? "1px solid var(--line)" : 0,
                  fontSize: 13.5
                }}>
                  <span style={{fontWeight:500}}>{row.p}</span>
                  <span className="mono" style={{color:"var(--ink-2)", fontSize:12.5}}>{row.m}</span>
                  <span className="mono" style={{color:"var(--ink-3)", fontSize:12}}>{row.v}</span>
                  <span style={{
                    fontSize:11, fontFamily:"var(--font-mono)", letterSpacing:"0.08em", textTransform:"uppercase",
                    color: row.state === "ready" ? "oklch(0.5 0.06 145)" : "oklch(0.55 0.06 75)"
                  }}>
                    <span style={{
                      display:"inline-block", width:6, height:6, borderRadius:999, marginRight:6,
                      background: row.state === "ready" ? "oklch(0.5 0.06 145)" : "oklch(0.55 0.06 75)"
                    }}/>
                    {row.state}
                  </span>
                  <span className="mono" style={{fontSize:11, color:"var(--ink-3)", textAlign:"right", cursor:"pointer"}}>configure ↗</span>
                </div>
              ))}
            </div>
          </section>

          <section className="sgroup">
            <h2 className="sgroup__t">Danger zone</h2>
            <p className="sgroup__s">Operations that cannot be undone. Take a snapshot first.</p>
            <div className="srow">
              <div>
                <div className="srow__l">Rebuild knowledge graph</div>
                <div className="srow__d">Re-extract entities and relationships from every conversation. Takes ~6 hours on this hardware.</div>
              </div>
              <button className="btn">Rebuild graph</button>
            </div>
            <div className="srow">
              <div>
                <div className="srow__l" style={{color:"oklch(0.45 0.12 30)"}}>Delete all local data</div>
                <div className="srow__d">Permanently destroy conversations, projects, the graph, and the embedding index.</div>
              </div>
              <button className="btn" style={{color:"oklch(0.45 0.12 30)", borderColor:"oklch(0.85 0.04 30)"}}>Delete everything</button>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
