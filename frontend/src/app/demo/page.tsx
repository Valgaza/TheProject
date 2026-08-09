"use client";

import React, { useState } from "react";
import { Icon } from "@/components/icons";
import { LiveGraph } from "@/components/live-graph";

const MEMORY_URL = process.env.NEXT_PUBLIC_MEMORY_URL ?? "http://localhost:8000";
const USER_ID = "user_001";

export default function Demo() {
  const [messages, setMessages] = useState<{ role: "user" | "ai"; id: number; text?: string }[]>([]);
  const [val, setVal] = useState("");

  const send = () => {
    if (!val.trim()) return;
    const id = Date.now();
    const text = val.trim();
    setMessages(m => [...m, { role: "user", id, text }]);
    setVal("");

    fetch(`${MEMORY_URL}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, user_id: USER_ID }),
    }).catch(err => console.error("[demo] ingest failed:", err));

    setTimeout(() => {
      setMessages(m => [...m, { role: "ai", id: id + 1 }]);
    }, 400);
  };

  return (
    <div className="demo">
      <div className="demo__chat">
        <div className="demo__chat-head">
          <div className="eyebrow">§ Live demo</div>
          <h2 className="demo__chat-title">Chat → Knowledge Graph</h2>
          <p className="demo__chat-sub">
            Send any message — names, projects, facts, relationships. Nexus extracts entities and
            stitches them into the graph on the right in real time.
          </p>
        </div>

        <div className="demo__messages">
          {messages.length === 0 && (
            <div className="demo__empty">
              <div className="display" style={{ fontSize: 22, color: "var(--ink-2)", marginBottom: 10 }}>
                Graph is ready.
              </div>
              <p style={{ fontSize: 13.5, color: "var(--ink-3)", margin: 0, lineHeight: 1.6 }}>
                Try something like:<br/>
                <em>"Sarah is the engineering lead at Nexus Labs and manages the Prism project."</em>
              </p>
            </div>
          )}
          {messages.map(m => (
            m.role === "user" ? (
              <div key={m.id} className="msg msg--user msg-enter">
                <div className="msg__hd">
                  <div className="msg__avatar">U</div>
                  <strong>You</strong>
                </div>
                <div className="msg__body"><p>{m.text}</p></div>
              </div>
            ) : (
              <div key={m.id} className="msg msg--ai msg-enter">
                <div className="msg__hd">
                  <div className="msg__avatar">N</div>
                  <strong>Nexus</strong>
                </div>
                <div className="msg__body">
                  <p style={{ color: "var(--ink-2)" }}>
                    Received — extracting entities and relationships. Watch the graph update on the right.
                  </p>
                </div>
              </div>
            )
          ))}
        </div>

        <div className="demo__composer">
          <div className="composer">
            <textarea
              className="composer__input"
              placeholder="Type anything — names, projects, facts, relationships…"
              value={val}
              onChange={e => setVal(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); }}
              rows={2}
            />
            <div className="composer__bot">
              <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>⌘↵ to send</span>
              <button className="btn btn--primary" onClick={send}>
                Send <Icon name="arrow" size={14}/>
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="demo__graph-wrapper">
        <LiveGraph />
      </div>
    </div>
  );
}
