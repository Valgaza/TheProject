"use client";

/**
 * One project: its conversations and its documents, together.
 *
 * This page exists because a project became a real container. Before
 * documents had tiers, opening a project could only ever mean "start a chat
 * filed under it", so /projects navigated straight to the composer. Now a
 * project holds two things a person needs to see, and neither belongs in a
 * modal or a separate tab.
 *
 * The two halves are the same boundary from two sides: what has been said
 * here, and what can be read here. Nothing on this page is visible from any
 * other project.
 */

import React, { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Icon } from "@/components/icons";
import { DocumentsPanel } from "@/components/documents-panel";
import { domainById } from "@/lib/domains";
import { useProjects } from "@/lib/project-context";
import {
  CHATS_CHANGED,
  createChat,
  listChats,
  notifyChatsChanged,
  type Chat,
} from "@/lib/api";

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const router = useRouter();
  const { projects, select } = useProjects();
  const [chats, setChats] = useState<Chat[]>([]);
  const [starting, setStarting] = useState(false);

  const project = projects.find(p => p.id === projectId) ?? null;

  // Opening a project makes it current, so the graph view and anything
  // started from here follow along without a second click.
  useEffect(() => {
    if (projectId) select(projectId);
  }, [projectId, select]);

  const load = useCallback(async () => {
    if (!projectId) return;
    try {
      setChats(await listChats(projectId));
    } catch {
      setChats([]);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
    window.addEventListener(CHATS_CHANGED, load);
    return () => window.removeEventListener(CHATS_CHANGED, load);
  }, [load]);

  const start = async () => {
    if (starting) return;
    setStarting(true);
    try {
      const chat = await createChat({ projectId });
      notifyChatsChanged();
      router.push(`/c/${chat.id}`);
    } finally {
      setStarting(false);
    }
  };

  const domain = domainById(project?.domain ?? "general");

  return (
    <div className="project">
      <header className="project__head">
        <div className="eyebrow">§ Project</div>
        <div className="project__title-row">
          <h1 className="project__title">{project?.name ?? "Project"}</h1>
          <span
            className="tag"
            style={{
              color: `var(--d-${domain.id})`,
              borderColor: `oklch(from var(--d-${domain.id}) 0.85 0.025 h)`,
            }}
          >
            <span className="dot" />
            {domain.id}
          </span>
        </div>
        {project?.description && <p className="project__desc">{project.description}</p>}
      </header>

      <div className="project__cols">
        <section className="project__chats">
          <header className="docs__head">
            <h2 className="docs__title">Conversations</h2>
            <p className="docs__note">
              These remember what you tell them here, and nowhere else.
            </p>
          </header>

          <button className="btn btn--primary" onClick={start} disabled={starting}>
            <Icon name="plus" size={14} /> New conversation
          </button>

          {chats.length === 0 ? (
            <p className="docs__empty">No conversations in this project yet.</p>
          ) : (
            <ul className="project__chatlist">
              {chats.map(chat => (
                <li key={chat.id}>
                  <button
                    className="project__chat"
                    onClick={() => router.push(`/c/${chat.id}`)}
                  >
                    <Icon name="chat" size={14} />
                    <span className="project__chatname">{chat.title}</span>
                    <span className="mono project__chatdate">
                      {new Date(chat.updated_at).toLocaleDateString()}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <DocumentsPanel projectId={projectId} projectName={project?.name} />
      </div>
    </div>
  );
}
