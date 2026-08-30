"use client";

/**
 * The starting screen: one composer, no pipeline picker.
 *
 * The four-card domain grid used to live here and made the user choose a
 * pipeline before typing. The orchestrator makes that call now, so the choice
 * is gone; the domains survive as labels on answers, in src/lib/domains.ts.
 */

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Composer } from "@/components/composer";
import { Icon } from "@/components/icons";
import { SUGGESTED } from "@/lib/domains";
import { createChat, defaultProject } from "@/lib/api";

export default function ChatHome() {
  const router = useRouter();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = async () => {
    const content = value.trim();
    if (!content) return;

    setBusy(true);
    setError(null);
    try {
      const project = await defaultProject();
      const chat = await createChat(project.id);
      // Carry the prompt to the thread rather than sending it here — the reply
      // takes a couple of seconds and it should arrive on the page you're
      // reading, not on the one you're leaving.
      router.push(`/c/${chat.id}?q=${encodeURIComponent(content)}`);
    } catch {
      setBusy(false);
      setError("Couldn't reach the core service on :8100. Is it running?");
    }
  };

  return (
    <div className="chathome">
      <div className="chathome__hero">
        <div className="eyebrow">§ Command</div>
        <h1 className="display chathome__title">
          Good afternoon.<br />
          <em>What</em> are we making?
        </h1>
        <p className="chathome__sub">
          Just start typing — Nexus routes the prompt to the right handler on its own.
        </p>
      </div>

      <div className="chathome__suggest">
        <div className="chathome__strip">
          <span className="eyebrow">§ Suggested</span>
          <span className="hr-line" />
        </div>
        <div className="chips">
          {SUGGESTED.map((s, i) => (
            <button key={i} className="chip" onClick={() => setValue(s)}>
              <span className="chip__num mono">{String(i + 1).padStart(2, "0")}</span>
              <span className="chip__text">{s}</span>
              <span className="chip__arrow"><Icon name="arrow" size={14} /></span>
            </button>
          ))}
        </div>
      </div>

      <Composer
        value={value}
        onChange={setValue}
        onSubmit={start}
        placeholder="Ask Nexus something — or paste a document, link, or table…"
        busy={busy}
        autoFocus
        badge={
          <div className="tag tag--accent">
            <span className="dot" style={{ background: "var(--accent)" }} />
            auto-routed
          </div>
        }
      />

      {error && (
        <p className="mono" style={{ marginTop: 12, fontSize: 12, color: "var(--ink-3)" }}>
          {error}
        </p>
      )}
    </div>
  );
}
