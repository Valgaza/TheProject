"use client";

/**
 * The starting screen: one composer, no pipeline picker.
 *
 * The four-card domain grid used to live here and made the user choose a
 * pipeline before typing. The orchestrator makes that call now, so the choice
 * is gone; the domains survive as labels on answers, in src/lib/domains.ts.
 *
 * A chat created here has no project (2026-09-02) -- POST /chats, not
 * POST /projects/{id}/chats. The only way to get a chat filed under a
 * project is to open it from the Projects page (?project=<id> below) or
 * move an existing chat into one later (the chat menu's "Move to
 * project"). The project switcher in the sidebar is just a view filter
 * for "Recent" -- it no longer decides where a new chat lands.
 */

import React, { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Composer } from "@/components/composer";
import { Icon } from "@/components/icons";
import { SUGGESTED } from "@/lib/domains";
import { createChat, createUnfiledChat } from "@/lib/api";
import { useProjects } from "@/lib/project-context";

export default function ChatHome() {
  // useSearchParams opts everything above it out of prerendering unless it
  // sits under a boundary -- same reason c/[chatId]/page.tsx wraps its own
  // read of `?q=` this way.
  return (
    <Suspense fallback={null}>
      <ChatHomeInner />
    </Suspense>
  );
}

function ChatHomeInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { projects } = useProjects();
  // Set only when this page was reached by opening a project (Projects
  // page's card click passes ?project=<id>) -- bare /chat, from the
  // sidebar's "New chat" or the landing page, carries none.
  const projectId = searchParams.get("project");
  const project = projectId ? projects.find(p => p.id === projectId) : undefined;

  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = async () => {
    const content = value.trim();
    if (!content) return;

    setBusy(true);
    setError(null);
    try {
      const chat = projectId ? await createChat(projectId) : await createUnfiledChat();
      // Carry the prompt to the thread rather than sending it here — the reply
      // takes a couple of seconds and it should arrive on the page you're
      // reading, not on the one you're leaving. The chat picks up its real,
      // model-written title (chat_service.py) and shows up in "Recent" once
      // that reply lands — see notifyChatsChanged() in c/[chatId]/page.tsx.
      router.push(`/c/${chat.id}?q=${encodeURIComponent(content)}`);
    } catch {
      setBusy(false);
      setError("Couldn't reach the core service on :8100. Is it running?");
    }
  };

  return (
    <div className="chathome">
      <div className="chathome__hero">
        <div className="eyebrow">
          {project ? `§ ${project.name}` : "§ Command"}
        </div>
        <h1 className="display chathome__title">
          Good afternoon.<br />
          <em>What</em> are we making?
        </h1>
        <p className="chathome__sub">
          {project
            ? `Just start typing — this chat will be filed under ${project.name}.`
            : "Just start typing — Nexus routes the prompt to the right handler on its own."}
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
        projectId={projectId ?? undefined}
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
