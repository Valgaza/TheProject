"use client";

/**
 * The starting screen: one composer, nothing else.
 *
 * A pipeline picker used to live here, then a row of suggested prompts. Both
 * were the screen telling the user what to do. The orchestrator picks the
 * handler and the person picks the question.
 */

import React, { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Composer } from "@/components/composer";
import { createChat } from "@/lib/api";
import { useProjects } from "@/lib/project-context";

export default function ChatHome() {
  // useSearchParams opts everything above it out of prerendering unless it
  // sits under a boundary. Next's docs are explicit about wrapping it.
  return (
    <Suspense fallback={<div className="chathome" />}>
      <Start />
    </Suspense>
  );
}

function Start() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { projects } = useProjects();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [web, setWeb] = useState(false);

  // A project only when the rail asked for one. Defaulting to whatever project
  // happens to be selected would file passing questions into a body of work,
  // and a project is a memory scope — that write is not free.
  const projectId = searchParams.get("project");
  const project = projects.find(p => p.id === projectId);

  const start = async () => {
    const content = value.trim();
    if (!content) return;

    setBusy(true);
    setError(null);
    try {
      const chat = await createChat({ projectId });
      // Carry the prompt to the thread rather than sending it here — the reply
      // takes a couple of seconds and it should arrive on the page you are
      // reading, not the one you are leaving.
      router.push(`/c/${chat.id}?q=${encodeURIComponent(content)}${web ? "&web=1" : ""}`);
    } catch {
      setBusy(false);
      setError("Couldn't reach the core service on :8100. Is it running?");
    }
  };

  return (
    <div className="chathome">
      <div className="chathome__mid">
        <div className="chathome__hero">
          <div className="eyebrow">§ Command</div>
          <h1 className="display chathome__title">
            <em>What</em> are we making?
          </h1>
        </div>
      </div>

      <div className="chathome__dock">
        <Composer
          value={value}
          onChange={setValue}
          onSubmit={start}
          placeholder="Ask Nexus something…"
          busy={busy}
          autoFocus
          projectId={projectId}
          web={web}
          onWebChange={setWeb}
          badge={
            <div className="tag tag--accent">
              <span className="dot" style={{
                background: project ? `var(--d-${project.domain})` : "var(--accent)",
              }} />
              {project ? project.name : "no project"}
            </div>
          }
        />
        {error && (
          <p className="mono" style={{ marginTop: 10, fontSize: 12, color: "var(--ink-3)" }}>
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
