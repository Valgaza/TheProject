"use client";

/**
 * The one input box.
 *
 * The tool buttons are the adapter affordances — attach a document or an
 * image, search the web, talk, pull in knowledge. Knowledge is still inert.
 * Images can also be pasted or dropped in. Each is uploaded the moment it is
 * attached (which is also what tells the runtime to load the vision model),
 * and goes with the next message sent. They stay visible
 * because the whole product direction is that you type, attach or speak here
 * and the system works out what to do, rather than making you pick a pipeline
 * first.
 */

import React, { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/icons";
import { ApiError, type ImageMeta, uploadDocument, uploadImage } from "@/lib/api";

const MAX_IMAGES = 4;

type PendingImage = {
  key: string;
  name: string;
  preview: string;
  status: "uploading" | "ready" | "error";
  meta?: ImageMeta;
  detail?: string;
};

const isImage = (file: File) => file.type.startsWith("image/") || /\.(heic|heif)$/i.test(file.name);

type Upload = {
  id: string;
  name: string;
  status: "uploading" | "done" | "error";
  detail?: string;
};

type ComposerProps = {
  value: string;
  onChange: (value: string) => void;
  /** Called with the images attached to this message (none if the page
   * doesn't take images). */
  onSubmit: (images: ImageMeta[]) => void;
  placeholder?: string;
  rows?: number;
  hint?: string;
  /** Shown in place of Send while a turn is in flight. */
  busy?: boolean;
  autoFocus?: boolean;
  /** What answered, if anything has yet — a label, not a control. */
  badge?: React.ReactNode;
  /** Where an attached file is filed. Null or absent puts it in the user's
   * own tier, readable from every conversation. */
  projectId?: string | null;
  /** Whether this message goes to the web too. The toggle only appears when
   * a page handles it, and the page resets it after each send. */
  web?: boolean;
  onWebChange?: (on: boolean) => void;
  /** Starts a hands-free voice conversation. The mic is inert without it. */
  onVoice?: () => void;
  /** Whether images can be attached: only where the page sends them on. */
  allowImages?: boolean;
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
  projectId = null,
  web = false,
  onWebChange,
  onVoice,
  allowImages = false,
}: ComposerProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [images, setImages] = useState<PendingImage[]>([]);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (autoFocus) inputRef.current?.focus();
  }, [autoFocus]);

  const ready = images.filter(i => i.status === "ready" && i.meta).map(i => i.meta as ImageMeta);
  const uploadingImages = images.some(i => i.status === "uploading");
  const canSend = !busy && !uploadingImages && (value.trim().length > 0 || ready.length > 0);

  const submit = () => {
    if (!canSend) return;
    onSubmit(ready);
    images.forEach(i => URL.revokeObjectURL(i.preview));
    setImages([]);
  };

  const removeImage = (key: string) =>
    setImages(prev => {
      prev.filter(i => i.key === key).forEach(i => URL.revokeObjectURL(i.preview));
      return prev.filter(i => i.key !== key);
    });

  const attachImage = async (file: File) => {
    const key = `${file.name}-${Date.now()}-${Math.random()}`;
    setImages(prev => [...prev, { key, name: file.name, preview: URL.createObjectURL(file), status: "uploading" }]);
    try {
      const meta = await uploadImage(file);
      setImages(prev => prev.map(i => (i.key === key ? { ...i, status: "ready", meta } : i)));
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : "upload failed";
      setImages(prev => prev.map(i => (i.key === key ? { ...i, status: "error", detail } : i)));
    }
  };

  const handleFiles = async (files: FileList | File[] | null) => {
    if (!files || files.length === 0) return;
    let room = MAX_IMAGES - images.length;
    for (const file of Array.from(files)) {
      if (allowImages && isImage(file)) {
        if (room-- > 0) void attachImage(file);
        continue;
      }
      const id = `${file.name}-${Date.now()}`;
      setUploads(prev => [...prev, { id, name: file.name, status: "uploading" }]);
      try {
        const doc = await uploadDocument(file, projectId);
        setUploads(prev =>
          prev.map(u =>
            u.id === id
              ? {
                  ...u,
                  status: doc.status === "failed" ? "error" : "done",
                  detail: doc.status === "failed"
                    ? doc.error ?? "indexing failed"
                    : `${doc.chunk_count} chunk${doc.chunk_count === 1 ? "" : "s"} indexed`,
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
      <div
        className={`composer ${dragging ? "is-dragging" : ""}`}
        onDragOver={e => {
          if (!allowImages || !e.dataTransfer.types.includes("Files")) return;
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => {
          if (!allowImages) return;
          e.preventDefault();
          setDragging(false);
          void handleFiles(e.dataTransfer.files);
        }}
      >
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
          onPaste={e => {
            const pasted = Array.from(e.clipboardData.files).filter(isImage);
            if (!allowImages || pasted.length === 0) return;
            e.preventDefault();
            void handleFiles(pasted);
          }}
          rows={rows}
          disabled={busy}
        />
        {images.length > 0 && (
          <div className="composer__images">
            {images.map(i => (
              <div key={i.key} className={`image-chip image-chip--${i.status}`} title={i.detail ?? i.name}>
                {/* eslint-disable-next-line @next/next/no-img-element -- a local blob preview */}
                <img src={i.preview} alt={i.name} />
                {i.status === "uploading" && <span className="image-chip__veil"><Icon name="spark" size={14} className="upload-chip__spin" /></span>}
                {i.status === "error" && <span className="image-chip__veil image-chip__veil--error">{i.detail}</span>}
                <button type="button" className="image-chip__remove" aria-label={`Remove ${i.name}`}
                        onClick={() => removeImage(i.key)}>
                  <Icon name="close" size={11} />
                </button>
              </div>
            ))}
          </div>
        )}
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
              accept={allowImages ? ".pdf,.md,.markdown,.mdx,image/*,.heic,.heif" : ".pdf,.md,.markdown,.mdx"}
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
              title={allowImages ? "Attach an image, or a document (.pdf, .md) for the project" : "Attach a document (.pdf, .md)"}
              onClick={() => fileInputRef.current?.click()}
            >
              <Icon name="paper" size={16} />
            </button>
            {onWebChange && (
              <button
                className={`ctool ${web ? "is-on" : ""}`}
                type="button"
                aria-pressed={web}
                title={web
                  ? "Web search on for this message. Names and ID numbers are stripped from the query first."
                  : "Search the web for this message"}
                onClick={() => onWebChange(!web)}
              >
                <Icon name="globe" size={16} />
              </button>
            )}
            <button
              className="ctool"
              type="button"
              title={onVoice ? "Talk: hands-free, speak over the answer to interrupt" : "Speak"}
              disabled={!onVoice || busy}
              onClick={onVoice}
            >
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
            disabled={!canSend}
          >
            {busy ? "Thinking…" : <>Send <Icon name="arrow" size={14} /></>}
          </button>
        </div>
      </div>
    </div>
  );
}
