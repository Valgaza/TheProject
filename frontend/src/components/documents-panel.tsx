"use client";

/**
 * The files that answer questions here.
 *
 * One component, two tiers. Inside a project it shows that project's
 * documents, which only that project's chats can retrieve. On its own it
 * shows the universal ones, readable from every conversation. The difference
 * is a single prop, because it is a single prop in the data too.
 *
 * Replaced the /rag-demo page. That page existed because documents belonged
 * to nobody -- there was no project to put them in, so they got a tab. Once
 * a file has an owner and a tier, the tab is the wrong shape: files belong
 * beside the chats that can read them.
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  DOCUMENTS_CHANGED,
  deleteDocument,
  listDocuments,
  notifyDocumentsChanged,
  uploadDocument,
  type Document,
} from "@/lib/api";
import { Icon } from "@/components/icons";

const ACCEPT = ".pdf,.md,.markdown,.mdx";

function readableSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

export function DocumentsPanel({
  projectId = null,
  projectName,
}: {
  projectId?: string | null;
  projectName?: string;
}) {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setDocuments(
        await listDocuments(projectId ? { projectId } : { loose: true })
      );
    } catch {
      setDocuments([]);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
    window.addEventListener(DOCUMENTS_CHANGED, load);
    return () => window.removeEventListener(DOCUMENTS_CHANGED, load);
  }, [load]);

  const send = useCallback(
    async (files: FileList | File[]) => {
      setError(null);
      const list = Array.from(files);
      if (!list.length) return;

      setBusy(b => [...b, ...list.map(f => f.name)]);
      for (const file of list) {
        try {
          await uploadDocument(file, projectId);
        } catch (e) {
          // One bad file should not abandon the rest of a dropped batch.
          setError(
            e instanceof Error && e.message
              ? `${file.name}: ${e.message}`
              : `${file.name} couldn't be read.`
          );
        }
      }
      setBusy(b => b.filter(name => !list.some(f => f.name === name)));
      await load();
      notifyDocumentsChanged();
    },
    [projectId, load]
  );

  const remove = useCallback(
    async (document: Document) => {
      setBusy(b => [...b, document.id]);
      try {
        await deleteDocument(document.id);
      } catch {
        setError(`${document.file_name} couldn't be removed.`);
      }
      setBusy(b => b.filter(id => id !== document.id));
      await load();
    },
    [load]
  );

  const scopeNote = projectId
    ? `Only chats in ${projectName ?? "this project"} can read these.`
    : "Readable from every chat, in every project.";

  return (
    <section className="docs">
      <header className="docs__head">
        <h2 className="docs__title">Documents</h2>
        <p className="docs__note">{scopeNote}</p>
      </header>

      <div
        className={`docs__drop ${dragging ? "is-over" : ""}`}
        onDragOver={e => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => {
          e.preventDefault();
          setDragging(false);
          void send(e.dataTransfer.files);
        }}
        onClick={() => input.current?.click()}
        onKeyDown={e => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            input.current?.click();
          }
        }}
        role="button"
        tabIndex={0}
        aria-label="Add documents"
      >
        <Icon name="paper" />
        <p className="docs__droptitle">Drop a file, or click to choose</p>
        <p className="docs__dropsub mono">PDF · Markdown</p>
        <input
          ref={input}
          type="file"
          accept={ACCEPT}
          multiple
          hidden
          onChange={e => {
            if (e.target.files) void send(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {error && (
        <p className="docs__error" role="alert">
          {error}
        </p>
      )}

      {loading ? (
        <p className="docs__empty mono">loading…</p>
      ) : documents.length === 0 && busy.length === 0 ? (
        <p className="docs__empty">
          Nothing here yet. Anything you add becomes searchable in{" "}
          {projectId ? "this project's chats" : "every chat"}.
        </p>
      ) : (
        <ul className="docs__list">
          {busy
            .filter(name => !documents.some(d => d.id === name))
            .map(name => (
              <li className="doc is-busy" key={`pending-${name}`}>
                <Icon name="paper" />
                <span className="doc__name">{name}</span>
                <span className="doc__state mono">reading…</span>
              </li>
            ))}

          {documents.map(document => (
            <li
              className={`doc ${document.status === "failed" ? "is-failed" : ""}`}
              key={document.id}
            >
              <Icon name="paper" />
              <span className="doc__name" title={document.file_name}>
                {document.file_name}
              </span>

              {document.status === "failed" ? (
                // The reason, not just the verdict. "Couldn't be read" with
                // the cause hidden in a tooltip is unactionable -- the whole
                // point of keeping a failed row is that someone can see what
                // went wrong and fix it.
                <span className="doc__state is-error">
                  {document.error || "couldn't be read"}
                </span>
              ) : (
                <span className="doc__state mono">
                  {document.chunk_count}{" "}
                  {document.chunk_count === 1 ? "passage" : "passages"} ·{" "}
                  {readableSize(document.byte_size)}
                </span>
              )}

              {/* A document reaches the graph a moment after it is stored --
                  cataloguing runs after the response, like the write gate
                  runs after a reply. Showing the difference stops the pause
                  reading as a failure. */}
              {document.status === "ingested" && (
                <span
                  className={`doc__graph ${document.graph_synced_at ? "is-synced" : ""}`}
                  title={
                    document.graph_synced_at
                      ? "This document's topics are in your knowledge graph"
                      : "Still being added to your knowledge graph"
                  }
                >
                  {document.graph_synced_at ? "in graph" : "indexing"}
                </span>
              )}

              <button
                className="doc__remove"
                type="button"
                onClick={() => void remove(document)}
                disabled={busy.includes(document.id)}
                aria-label={`Remove ${document.file_name}`}
              >
                <Icon name="close" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
