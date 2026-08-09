"use client";

import React, { useRef, useState, useEffect } from "react";
import { Icon } from "@/components/icons";

const API_BASE = "http://localhost:8000";

interface SourceInfo {
  file_name: string;
  page_number: number | string | null;
  chunk_index: number | string | null;
  heading_path: string;
  rerank_score: number;
}

interface IngestResult {
  success: boolean;
  file_name: string;
  documents_processed: number;
  chunks_created: number;
  embeddings_generated: number;
  points_upserted: number;
  elapsed_seconds: number;
  errors: string[];
}

interface ChatMessage {
  id: number;
  role: "user" | "system";
  text: string;
  // For system messages with RAG results
  prompt?: string;
  sources?: SourceInfo[];
  stats?: {
    total_chunks_used: number;
    total_chars: number;
    retrieval_count: number;
    reranked_count: number;
    elapsed_seconds: number;
  };
  // For ingest confirmations
  ingest?: IngestResult;
}

export default function RagDemo() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [ingestedFiles, setIngestedFiles] = useState<string[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // ── File upload / ingest ─────────────────────────────────────────────
  const handleIngest = async (file: File) => {
    setUploading(true);
    const msgId = Date.now();

    setMessages((m) => [
      ...m,
      {
        id: msgId,
        role: "system",
        text: `Uploading and ingesting **${file.name}**…`,
      },
    ]);

    try {
      const form = new FormData();
      form.append("file", file);

      const res = await fetch(`${API_BASE}/api/ingest`, {
        method: "POST",
        body: form,
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || res.statusText);
      }

      const data: IngestResult = await res.json();

      setMessages((m) =>
        m.map((msg) =>
          msg.id === msgId
            ? {
                ...msg,
                text: data.success
                  ? `Ingested **${data.file_name}** successfully.`
                  : `Ingestion had errors for **${data.file_name}**.`,
                ingest: data,
              }
            : msg
        )
      );

      if (data.success) {
        setIngestedFiles((f) => [...new Set([...f, data.file_name])]);
      }
    } catch (err: any) {
      setMessages((m) =>
        m.map((msg) =>
          msg.id === msgId
            ? { ...msg, text: `Error ingesting **${file.name}**: ${err.message}` }
            : msg
        )
      );
    } finally {
      setUploading(false);
    }
  };

  const onFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleIngest(file);
    e.target.value = "";
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleIngest(file);
  };

  // ── Query ────────────────────────────────────────────────────────────
  const handleQuery = async () => {
    const q = query.trim();
    if (!q || loading) return;

    const userMsgId = Date.now();
    const aiMsgId = userMsgId + 1;

    setMessages((m) => [
      ...m,
      { id: userMsgId, role: "user", text: q },
      { id: aiMsgId, role: "system", text: "Searching knowledge base…" },
    ]);
    setQuery("");
    setLoading(true);

    try {
      const form = new FormData();
      form.append("question", q);

      const res = await fetch(`${API_BASE}/api/query`, {
        method: "POST",
        body: form,
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || res.statusText);
      }

      const data = await res.json();

      setMessages((m) =>
        m.map((msg) =>
          msg.id === aiMsgId
            ? {
                ...msg,
                text: "Context retrieved — here's the assembled prompt that would go to the LLM:",
                prompt: data.assembled_prompt,
                sources: data.sources,
                stats: {
                  total_chunks_used: data.total_chunks_used,
                  total_chars: data.total_chars,
                  retrieval_count: data.retrieval_count,
                  reranked_count: data.reranked_count,
                  elapsed_seconds: data.elapsed_seconds,
                },
              }
            : msg
        )
      );
    } catch (err: any) {
      setMessages((m) =>
        m.map((msg) =>
          msg.id === aiMsgId
            ? { ...msg, text: `Error: ${err.message}` }
            : msg
        )
      );
    } finally {
      setLoading(false);
    }
  };

  // ── Render helpers ───────────────────────────────────────────────────
  const renderIngestStats = (data: IngestResult) => (
    <div className="rag-ingest-card">
      <div className="rag-ingest-grid">
        <div className="rag-stat">
          <span className="rag-stat__val">{data.documents_processed}</span>
          <span className="rag-stat__label">Documents</span>
        </div>
        <div className="rag-stat">
          <span className="rag-stat__val">{data.chunks_created}</span>
          <span className="rag-stat__label">Chunks</span>
        </div>
        <div className="rag-stat">
          <span className="rag-stat__val">{data.embeddings_generated}</span>
          <span className="rag-stat__label">Embeddings</span>
        </div>
        <div className="rag-stat">
          <span className="rag-stat__val">{data.points_upserted}</span>
          <span className="rag-stat__label">Vectors stored</span>
        </div>
      </div>
      <div className="rag-ingest-time mono">
        Completed in {data.elapsed_seconds}s
      </div>
      {data.errors.length > 0 && (
        <div className="rag-errors">
          {data.errors.map((e, i) => (
            <div key={i} className="rag-error">{e}</div>
          ))}
        </div>
      )}
    </div>
  );

  const renderPrompt = (prompt: string) => (
    <div className="rag-prompt-block">
      <div className="rag-prompt-header">
        <span className="rag-prompt-label eyebrow">§ Assembled LLM Prompt</span>
        <button
          className="rag-copy-btn"
          onClick={() => navigator.clipboard.writeText(prompt)}
          title="Copy prompt"
        >
          <Icon name="paper" size={13} /> Copy
        </button>
      </div>
      <pre className="rag-prompt-pre">{prompt}</pre>
    </div>
  );

  const renderSources = (sources: SourceInfo[]) => (
    <div className="rag-sources">
      <span className="rag-prompt-label eyebrow" style={{ marginBottom: 10, display: "block" }}>
        § Sources used
      </span>
      <table className="tbl">
        <thead>
          <tr>
            <th>#</th>
            <th>File</th>
            <th>Page</th>
            <th>Chunk</th>
            <th>Heading</th>
            <th>Score</th>
          </tr>
        </thead>
        <tbody>
          {sources.map((s, i) => (
            <tr key={i}>
              <td className="num">{i + 1}</td>
              <td>{s.file_name}</td>
              <td className="num">{s.page_number ?? "—"}</td>
              <td className="num">{s.chunk_index ?? "—"}</td>
              <td style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {s.heading_path || "—"}
              </td>
              <td className="num">{s.rerank_score.toFixed(4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  const renderStats = (stats: ChatMessage["stats"]) => {
    if (!stats) return null;
    return (
      <div className="trace" style={{ marginTop: 16 }}>
        <span>PIPELINE TRACE</span>
        <span className="trace__line" />
        <span className="trace__dots">
          <span className="trace__dot is-on" />
          <span style={{ color: "var(--ink-3)" }}>retrieve · {stats.retrieval_count} hits</span>
          <span className="trace__line" style={{ width: 24, flex: "0 0 24px" }} />
          <span className="trace__dot is-on" />
          <span style={{ color: "var(--ink-3)" }}>rerank · top {stats.reranked_count}</span>
          <span className="trace__line" style={{ width: 24, flex: "0 0 24px" }} />
          <span className="trace__dot is-on" />
          <span style={{ color: "var(--ink-3)" }}>
            assemble · {stats.total_chunks_used} chunks · {stats.total_chars} chars
          </span>
        </span>
        <span className="trace__line" />
        <span style={{ color: "var(--ink-3)" }}>{stats.elapsed_seconds}s</span>
      </div>
    );
  };

  return (
    <div className="rag-demo">
      {/* Header */}
      <div className="rag-demo__head">
        <div className="eyebrow">§ RAG Pipeline Demo</div>
        <h1 className="display rag-demo__title">
          Knowledge <em>Retrieval</em> Playground
        </h1>
        <p className="rag-demo__sub">
          Upload documents to ingest into the vector database, then ask questions.
          The assembled prompt (what would be sent to the LLM) is displayed below.
        </p>
      </div>

      {/* Ingested files status bar */}
      {ingestedFiles.length > 0 && (
        <div className="rag-status-bar">
          <Icon name="db" size={14} />
          <span className="mono" style={{ fontSize: 11, letterSpacing: "0.08em" }}>
            KNOWLEDGE BASE
          </span>
          <span className="trace__line" />
          {ingestedFiles.map((f) => (
            <span key={f} className="tag tag--accent">{f}</span>
          ))}
        </div>
      )}

      {/* Drop zone (only shown when no messages yet) */}
      {messages.length === 0 && (
        <div
          className={`rag-dropzone ${dragOver ? "is-over" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <div className="rag-dropzone__icon">
            <Icon name="arrowU" size={24} />
          </div>
          <div className="rag-dropzone__title">Drop a document here</div>
          <div className="rag-dropzone__sub">
            or click to browse · PDF, Markdown supported
          </div>
        </div>
      )}

      {/* Chat messages */}
      <div className="rag-chat">
        {messages.map((m) => (
          <div key={m.id} className={`msg msg--${m.role === "user" ? "user" : "ai"} msg-enter`}>
            <div className="msg__hd">
              <div className="msg__avatar">{m.role === "user" ? "U" : "R"}</div>
              <strong>{m.role === "user" ? "You" : "RAG Pipeline"}</strong>
              <span style={{ fontSize: 10, color: "var(--ink-4)" }}>
                {new Date(m.id).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </span>
            </div>
            <div className="msg__body">
              <p>{m.text}</p>
              {m.ingest && renderIngestStats(m.ingest)}
              {m.prompt && renderPrompt(m.prompt)}
              {m.sources && m.sources.length > 0 && renderSources(m.sources)}
              {m.stats && renderStats(m.stats)}
            </div>
          </div>
        ))}
        <div ref={chatEndRef} />
      </div>

      {/* Composer */}
      <div className="composer-wrap">
        <div className="composer">
          <div className="composer__top">
            <div className="tag tag--accent">
              <span className="dot" style={{ background: "var(--d-research)" }} />
              RAG Pipeline · Demo
            </div>
            <span className="composer__hint mono">⌘ + ⏎ to send</span>
          </div>
          <textarea
            ref={textareaRef}
            className="composer__input"
            placeholder={
              ingestedFiles.length > 0
                ? "Ask a question about your documents…"
                : "Upload a document first, then ask questions…"
            }
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleQuery();
            }}
            rows={2}
            disabled={loading}
          />
          <div className="composer__bot">
            <div className="composer__tools">
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,.md,.markdown,.mdx"
                style={{ display: "none" }}
                onChange={onFileSelect}
              />
              <button
                className="ctool"
                title="Upload document"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
              >
                <Icon name="paper" size={16} />
              </button>
              <button className="ctool" title="Knowledge base">
                <Icon name="db" size={16} />
              </button>
              <span className="composer__sep" />
              <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                {ingestedFiles.length} file{ingestedFiles.length !== 1 ? "s" : ""} ingested ·
                Qdrant hybrid search
              </span>
            </div>
            <button
              className="btn btn--primary"
              onClick={handleQuery}
              disabled={loading || !query.trim()}
            >
              {loading ? "Searching…" : "Query"} <Icon name="arrow" size={14} />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
