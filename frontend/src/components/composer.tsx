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

import React, { useEffect, useRef } from "react";
import { Icon } from "@/components/icons";

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
  hint = "⌘ + ⏎ to send",
  busy = false,
  autoFocus = false,
  badge,
}: ComposerProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (autoFocus) inputRef.current?.focus();
  }, [autoFocus]);

  const submit = () => {
    if (busy || !value.trim()) return;
    onSubmit();
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
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              submit();
            }
          }}
          rows={rows}
          disabled={busy}
        />
        <div className="composer__bot">
          <div className="composer__tools">
            <button className="ctool" type="button" title="Attach a document" disabled>
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
