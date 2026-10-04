"use client";

/**
 * One conversation, backed by the core service.
 *
 * Replaces the old hardcoded /conversation thread. Everything on this page is
 * real: the messages come from Postgres, the replies come from a model, and a
 * reload rebuilds the thread from the server.
 */

import React, { Suspense, use, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Composer } from "@/components/composer";
import { CitedSources } from "@/components/cited-sources";
import { WebSources } from "@/components/web-sources";
import { RenderedAnswer } from "@/components/rendered-answer";
import { RecalledFacts } from "@/components/recalled-facts";
import { Icon } from "@/components/icons";
import { domainById } from "@/lib/domains";
import {
  ApiError,
  type Chat,
  type Message,
  getChat,
  listMessages,
  notifyChatsChanged,
  sendMessage,
} from "@/lib/api";

type PendingMessage = Message & { pending?: true };

export default function ConversationPage({
  params,
}: {
  params: Promise<{ chatId: string }>;
}) {
  const { chatId } = use(params);

  // useSearchParams opts everything above it out of prerendering unless it sits
  // under a boundary. Next's docs are explicit about wrapping it.
  return (
    <Suspense fallback={<ThreadSkeleton />}>
      <Thread chatId={chatId} />
    </Suspense>
  );
}

function Thread({ chatId }: { chatId: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  // /chat hands the opening prompt over in the URL so this page owns the send,
  // and the user watches it land here instead of waiting on the previous screen.
  const opening = searchParams.get("q");
  const openingWeb = searchParams.get("web") === "1";

  const [chat, setChat] = useState<Chat | null>(null);
  const [messages, setMessages] = useState<PendingMessage[]>([]);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [web, setWeb] = useState(false);

  const bottomRef = useRef<HTMLDivElement>(null);
  // React 18+ runs effects twice in development; without this the opening
  // prompt gets sent twice and the thread starts with a duplicate.
  const sentOpening = useRef(false);

  const send = useCallback(
    async (content: string, useWeb = false) => {
      const trimmed = content.trim();
      if (!trimmed) return;

      setBusy(true);
      setError(null);
      const optimistic: PendingMessage = {
        id: `pending-${Date.now()}`,
        chat_id: chatId,
        role: "user",
        content: trimmed,
        metadata: {},
        created_at: new Date().toISOString(),
        pending: true,
      };
      setMessages(prev => [...prev, optimistic]);

      try {
        const turn = await sendMessage(chatId, trimmed, { useWeb });
        // Swap the optimistic row for the stored one; ids and timestamps are
        // the server's to assign.
        setMessages(prev => [
          ...prev.filter(m => m.id !== optimistic.id),
          turn.user_message,
          turn.assistant_message,
        ]);
        setChat(prev => (prev ? { ...prev, title: turn.chat_title } : prev));
        // The first message renames the chat and bumps its position; the rail
        // has no other way to know.
        notifyChatsChanged();
      } catch (err) {
        setMessages(prev => prev.filter(m => m.id !== optimistic.id));
        setValue(trimmed);
        setError(
          err instanceof ApiError
            ? `The core service returned ${err.status}.`
            : "Couldn't reach the core service on :8100."
        );
      } finally {
        setBusy(false);
      }
    },
    [chatId]
  );

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const [loadedChat, loadedMessages] = await Promise.all([
          getChat(chatId),
          listMessages(chatId),
        ]);
        if (cancelled) return;
        setChat(loadedChat);
        setMessages(loadedMessages);

        if (opening && !sentOpening.current && loadedMessages.length === 0) {
          sentOpening.current = true;
          void send(opening, openingWeb);
        }
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof ApiError && err.status === 404
            ? "That conversation doesn't exist."
            : "Couldn't reach the core service on :8100."
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [chatId, opening, openingWeb, send]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, busy]);

  const handled = messages.findLast(m => m.role === "assistant")?.metadata.route;
  const domain = domainById(handled ?? "general");

  if (loading) return <ThreadSkeleton />;

  return (
    <div className="convo">
      <div className="convo__scroll">
        <div className="convo__head">
          <div className="convo__meta">
            <span className="tag tag--accent">
              <span className="dot" style={{ background: `var(--d-${domain.id})` }} />
              {domain.name}
            </span>
            <span>{messages.length} messages</span>
            <span>·</span>
            <span>{domain.id} pipeline</span>
          </div>
          <h1 className="convo__title">{chat?.title ?? "Conversation"}</h1>
        </div>

        {messages.map(m =>
          m.role === "user" ? (
            <UserMessage key={m.id} message={m} />
          ) : (
            <AssistantMessage key={m.id} message={m} onViewGraph={() => router.push("/graph")} />
          )
        )}

        {busy && <ThinkingMessage />}

        {error && (
          <div className="msg msg--ai" style={{ color: "var(--d-vision, crimson)" }}>
            <div className="msg__body">
              <p>{error}</p>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Outside .convo__scroll on purpose -- a fixed dock, not part of the
          scrolling history, so it's always in reach instead of scrolling
          away with a long conversation. */}
      <div className="convo__composer-dock">
        <Composer
          value={value}
          onChange={setValue}
          onSubmit={() => {
            const content = value;
            setValue("");
            // One message at a time: leaving it on would send every later
            // message off the machine too, which nobody should do by accident.
            setWeb(false);
            void send(content, web);
          }}
          placeholder="Continue the conversation…"
          rows={2}
          busy={busy}
          projectId={chat?.project_id ?? null}
          web={web}
          onWebChange={setWeb}
          autoFocus
          badge={
            <div className="tag tag--accent">
              <span className="dot" style={{ background: `var(--d-${domain.id})` }} />
              {domain.name}
            </div>
          }
        />
      </div>
    </div>
  );
}

const ThreadSkeleton = () => (
  <div className="convo">
    <div className="skeleton" style={{ height: 20, width: 240, marginBottom: 24 }} />
    <div className="skeleton" style={{ height: 14, width: "80%", marginBottom: 8 }} />
    <div className="skeleton" style={{ height: 14, width: "62%" }} />
  </div>
);

const UserMessage = ({ message }: { message: PendingMessage }) => (
  <div className="msg msg--user msg-enter" style={{ opacity: message.pending ? 0.6 : 1 }}>
    <div className="msg__hd">
      <div className="msg__avatar">U</div>
      <strong>You</strong>
    </div>
    <div className="msg__body">
      <p style={{ whiteSpace: "pre-wrap" }}>{message.content}</p>
    </div>
  </div>
);

const AssistantMessage = ({
  message,
  onViewGraph,
}: {
  message: Message;
  onViewGraph: () => void;
}) => {
  const { route, route_reason, latency_ms, error } = message.metadata;
  const domain = domainById(route ?? "general");

  return (
    <div className="msg msg--ai msg-enter">
      <div className="msg__hd">
        <div className="msg__avatar">N</div>
        <strong>Nexus · {domain.name}</strong>
        <span>{new Date(message.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
        <span style={{ flex: 1 }} />
        {latency_ms != null && (
          <span style={{ color: "var(--ink-3)" }}>· {(latency_ms / 1000).toFixed(1)}s</span>
        )}
      </div>
      <div className="msg__body">
        <RenderedAnswer content={message.content} />
        <RecalledFacts facts={message.metadata.recalled_facts ?? []} />
        <CitedSources sources={message.metadata.rag_sources ?? []} />
        <WebSources web={message.metadata.web} />
        <div className="trace">
          <span>PIPELINE TRACE</span>
          <span className="trace__line" />
          <span className="trace__dots">
            <span className={`trace__dot ${error ? "" : "is-on"}`} />
            <span style={{ color: "var(--ink-3)" }} title={route_reason}>
              route · {route ?? "general"}
            </span>
            <span className="trace__line" style={{ width: 24, flex: "0 0 24px" }} />
            <span className={`trace__dot ${error ? "" : "is-on"}`} />
            <span style={{ color: "var(--ink-3)" }}>{error ? "failed" : "compose"}</span>
          </span>
          <span className="trace__line" />
          <span style={{ cursor: "pointer", color: "var(--accent)" }} onClick={onViewGraph}>
            view in graph ↗
          </span>
        </div>
      </div>
    </div>
  );
};

const ThinkingMessage = () => (
  <div className="msg msg--ai msg-enter">
    <div className="msg__hd">
      <div className="msg__avatar">N</div>
      <strong>Nexus</strong>
    </div>
    <div className="msg__body">
      <div className="skeleton" style={{ height: 14, width: "90%" }} />
      <div className="skeleton" style={{ height: 14, width: "72%", marginTop: 8 }} />
    </div>
  </div>
);
