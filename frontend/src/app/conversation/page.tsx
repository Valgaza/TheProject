"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/icons";

export default function Conversation() {
  const router = useRouter();
  const [messages, setMessages] = useState<any[]>([
    { role: "user", id: 1, text: "Draft the opening section of our Q3 board narrative — the one we'll send Friday. Lean on the EU pipeline-cost variance work from last week and keep the tone confident, not defensive." },
    { role: "ai", id: 2, pipeline: "finance", finished: true, hops: ["router","retrieve","compose","cite"] },
  ]);
  const [streaming, setStreaming] = useState("");
  const [val, setVal] = useState("");

  useEffect(() => {
    setTimeout(() => setStreaming("done"), 300);
  }, []);

  const send = () => {
    if (!val.trim()) return;
    const id = Date.now();
    setMessages(m => [...m, { role: "user", id, text: val.trim() }]);
    setVal("");
    setTimeout(() => {
      setMessages(m => [...m, { role: "ai", id: id + 1, pipeline: "finance", finished: true, followup: true }]);
    }, 600);
  };

  return (
    <div className="convo">
      <div className="convo__head">
        <div className="convo__meta">
          <span className="tag tag--accent">
            <span className="dot" style={{background: "var(--d-finance)"}}/>
            FINANCE PIPELINE
          </span>
          <span>3 messages</span>
          <span>·</span>
          <span>started 2h ago</span>
          <span>·</span>
          <span>llama-3.1-70b-q4 · local</span>
        </div>
        <h1 className="convo__title">Q3 board narrative draft</h1>
      </div>

      {messages.map(m => m.role === "user"
        ? <UserMsg key={m.id} text={m.text}/>
        : <AIMsg key={m.id} m={m} streaming={streaming} />)}

      <div className="composer-wrap" style={{ marginTop: 32 }}>
        <div className="composer">
          <div className="composer__top">
            <div className="tag tag--accent">
              <span className="dot" style={{background: "var(--d-finance)"}}/>
              Finance pipeline
            </div>
            <span className="composer__hint mono">↑ to edit last · ⌘K to switch</span>
          </div>
          <textarea
            className="composer__input"
            placeholder="Continue the conversation…"
            value={val}
            onChange={e => setVal(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); }}
            rows={2}
          />
          <div className="composer__bot">
            <div className="composer__tools">
              <button className="ctool"><Icon name="paper" size={16}/></button>
              <button className="ctool"><Icon name="mic" size={16}/></button>
              <button className="ctool"><Icon name="book" size={16}/></button>
            </div>
            <button className="btn btn--primary" onClick={send}>Send <Icon name="arrow" size={14}/></button>
          </div>
        </div>
      </div>
    </div>
  );
}

const UserMsg = ({ text }: { text: string }) => (
  <div className="msg msg--user msg-enter">
    <div className="msg__hd">
      <div className="msg__avatar">AK</div>
      <strong>Aditya</strong>
      <span>14:32</span>
    </div>
    <div className="msg__body"><p>{text}</p></div>
  </div>
);

const AIMsg = ({ m, streaming }: any) => {
  const router = useRouter();
  return (
    <div className="msg msg--ai msg-enter" style={{ animationDelay: m.followup ? "0ms" : "200ms"}}>
      <div className="msg__hd">
        <div className="msg__avatar">N</div>
        <strong>Nexus · Finance</strong>
        <span>14:32</span>
        <span style={{flex:1}}/>
        <span style={{color: "var(--ink-3)"}}>· 2.4s · 1,247 tokens</span>
      </div>
      {m.followup ? (
        <div className="msg__body">
          <p>Working on that section now — pulling the EU variance numbers and last-quarter context. Three paragraphs incoming.</p>
          <div style={{display:"flex", gap:8, marginTop: 12}}>
            <div className="skeleton" style={{height: 14, width: "92%"}}/>
          </div>
          <div style={{display:"flex", gap:8, marginTop: 8}}>
            <div className="skeleton" style={{height: 14, width: "78%"}}/>
          </div>
        </div>
      ) : (
        <div className="msg__body">
          <h4>Q3 — A quarter of disciplined ambition.</h4>
          <p>
            The third quarter delivered <strong>$48.2M in net revenue</strong>, a 31% lift over Q2 and the strongest sequential growth since the platform launched
            <span className="cite">1</span>. Beneath that headline, the texture is cleaner than the number alone suggests: gross margin expanded 280 basis points to 71.4% as our local-inference deployments matured past the early operational tax<span className="cite">2</span>, and the EU pipeline-cost variance flagged in Q2 has been substantially absorbed.
          </p>

          <p>
            The EU correction is worth dwelling on. We over-provisioned compute against a forecast that assumed a heavier mix of long-context inference; in practice, the workload skewed toward retrieval-augmented patterns that the smaller pipelines handle natively. We reclaimed
            <strong> €1.4M in run-rate spend</strong> by re-routing 38% of EU traffic to the Finance and Operations pipelines and decommissioning two over-sized clusters in Frankfurt-2.
          </p>

          <table className="tbl">
            <thead>
              <tr><th>Region</th><th>Forecast Q3</th><th>Actual Q3</th><th>Variance</th></tr>
            </thead>
            <tbody>
              <tr><td>NA</td><td className="num">$18.4M</td><td className="num">$19.1M</td><td className="num" style={{color: "oklch(0.5 0.06 145)"}}>+3.8%</td></tr>
              <tr><td>EU</td><td className="num">$15.8M</td><td className="num">$14.3M</td><td className="num" style={{color: "oklch(0.5 0.07 30)"}}>−9.5%</td></tr>
              <tr><td>APAC</td><td className="num">$11.2M</td><td className="num">$12.8M</td><td className="num" style={{color: "oklch(0.5 0.06 145)"}}>+14.3%</td></tr>
              <tr><td>LATAM</td><td className="num">$1.8M</td><td className="num">$2.0M</td><td className="num" style={{color: "oklch(0.5 0.06 145)"}}>+11.1%</td></tr>
            </tbody>
          </table>

          <p>
            Looking forward, the question for Q4 is not whether we can grow — the pipeline supports it — but whether we can grow without re-introducing the operational drag of 2025. The answer, candidly, depends on three calls the board should weigh in on this quarter.
          </p>

          <pre className="code"><span className="c">// suggested closing — needs review</span>{"\n"}<span className="k">summary</span>: {"{"}{"\n"}  <span className="k">posture</span>: <span className="s">"confident_not_defensive"</span>,{"\n"}  <span className="k">runway</span>: <span className="n">14.2</span>, <span className="c">// quarters</span>{"\n"}  <span className="k">asks</span>: [<span className="s">"EU_recommit"</span>, <span className="s">"APAC_expand"</span>, <span className="s">"hire_freeze_lift"</span>]{"\n"}{"}"}</pre>

          <div className="trace">
            <span>PIPELINE TRACE</span>
            <span className="trace__line"/>
            <span className="trace__dots">
              <span className="trace__dot is-on" title="Router"/>
              <span style={{color:"var(--ink-3)"}}>router</span>
              <span className="trace__line" style={{width: 24, flex: "0 0 24px"}}/>
              <span className="trace__dot is-on"/>
              <span style={{color:"var(--ink-3)"}}>retrieve · 14 docs</span>
              <span className="trace__line" style={{width: 24, flex: "0 0 24px"}}/>
              <span className="trace__dot is-on"/>
              <span style={{color:"var(--ink-3)"}}>finance · q4-70b</span>
              <span className="trace__line" style={{width: 24, flex: "0 0 24px"}}/>
              <span className="trace__dot is-on"/>
              <span style={{color:"var(--ink-3)"}}>cite · 2 sources</span>
            </span>
            <span className="trace__line"/>
            <span style={{cursor: "pointer", color: "var(--accent)"}} onClick={() => router.push("/graph")}>view in graph ↗</span>
          </div>
        </div>
      )}
    </div>
  );
};
