"use client";

/**
 * The one input box.
 *
 * The three tool buttons are the adapter affordances — attach a document, speak,
 * pull in knowledge. They are inert this milestone; the pipelines behind them
 * belong to phases and people other than this one. They stay visible because
 * the whole product direction is that you type or attach here and the system
 * works out what to do, rather than making you pick a pipeline first.
 */

import React, { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/icons";
import { ApiError, uploadDocument } from "@/lib/api";

type Upload = {
  id: string;
  name: string;
  status: "uploading" | "done" | "error";
  detail?: string;
};

type ComposerProps = {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  placeholder?: string;
  rows?: number;
  hint?: string;
  /** Shown in place of Send while a turn is in flight. */
  busy?: boolean;
  autoFocus?: boolean;
  /** What answered, if anything has yet — a label, not a control. */
  badge?: React.ReactNode;
};

export function Composer({
  value,
  onChange,
  onSubmit,
  placeholder = "Ask Nexus something…",
  rows = 3,
  hint = "⏎ to send · shift + ⏎ for a new line",
  busy = false,
  autoFocus = false,
  badge,
}: ComposerProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploads, setUploads] = useState<Upload[]>([]);

  useEffect(() => {
    if (autoFocus) inputRef.current?.focus();
  }, [autoFocus]);

  const submit = () => {
    if (busy || !value.trim()) return;
    onSubmit();
  };

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    for (const file of Array.from(files)) {
      const id = `${file.name}-${Date.now()}`;
      setUploads(prev => [...prev, { id, name: file.name, status: "uploading" }]);
      try {
        const result = await uploadDocument(file);
        setUploads(prev =>
          prev.map(u =>
            u.id === id
              ? {
                  ...u,
                  status: "done",
                  detail: `${result.chunks_created} chunk${result.chunks_created === 1 ? "" : "s"} indexed`,
                }
              : u
          )
        );
      } catch (err) {
        setUploads(prev =>
          prev.map(u =>
            u.id === id
              ? { ...u, status: "error", detail: err instanceof ApiError ? err.message : "upload failed" }
              : u
          )
        );
      }
    }
  };

  return (
    <div className="composer-wrap">
      <div className="composer">
        {(badge || hint) && (
          <div className="composer__top">
            {badge ?? <span />}
            {hint && <span className="composer__hint mono">{hint}</span>}
          </div>
        )}
        <textarea
          ref={inputRef}
          className="composer__input"
          placeholder={placeholder}
          value={value}
          onChange={e => onChange(e.target.value)}
          onKeyDown={e => {
            // Enter sends; shift+enter (or any other modifier) inserts a
            // newline like every other chat app -- cmd/ctrl+enter used to
            // be the only way to send, which meant a bare Enter did nothing
            // visible at all (multiline textarea, no newline inserted
            // either) and looked broken.
            if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
              e.preventDefault();
              submit();
            }
          }}
          rows={rows}
          disabled={busy}
        />
        {uploads.length > 0 && (
          <div className="composer__uploads">
            {uploads.map(u => (
              <div key={u.id} className={`upload-chip upload-chip--${u.status}`}>
                <Icon
                  name={u.status === "done" ? "check" : u.status === "error" ? "close" : "paper"}
                  size={13}
                  className={u.status === "uploading" ? "upload-chip__spin" : undefined}
                />
                <span className="upload-chip__name">{u.name}</span>
                {u.detail && <span className="upload-chip__detail">{u.detail}</span>}
                {u.status !== "uploading" && (
                  <button
                    type="button"
                    className="upload-chip__dismiss"
                    aria-label="Dismiss"
                    onClick={() => setUploads(prev => prev.filter(x => x.id !== u.id))}
                  >
                    <Icon name="close" size={11} />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
        <div className="composer__bot">
          <div className="composer__tools">
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.md,.markdown,.mdx"
              multiple
              style={{ display: "none" }}
              onChange={e => {
                void handleFiles(e.target.files);
                // Reset so selecting the same file again still fires onChange.
                e.target.value = "";
              }}
            />
            <button
              className="ctool"
              type="button"
              title="Attach a document (.pdf, .md)"
              onClick={() => fileInputRef.current?.click()}
            >
              <Icon name="paper" size={16} />
            </button>
            <button className="ctool" type="button" title="Speak" disabled>
              <Icon name="mic" size={16} />
            </button>
            <button className="ctool" type="button" title="Knowledge" disabled>
              <Icon name="book" size={16} />
            </button>
          </div>
          <button
            className="btn btn--primary"
            type="button"
            onClick={submit}
            disabled={busy || !value.trim()}
          >
            {busy ? "Thinking…" : <>Send <Icon name="arrow" size={14} /></>}
          </button>
        </div>
      </div>
    </div>
  );
}
