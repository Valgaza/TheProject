"use client";

/**
 * What a web search sent, and what came back.
 *
 * The query shown is the one that actually left the machine -- the redacted
 * rewrite, not what was typed. Showing it is the point: it is the only way
 * someone can check that a name they typed did not go out with it.
 */

import React, { useState } from "react";
import type { WebMeta } from "@/lib/api";

export function WebSources({ web }: { web?: WebMeta }) {
  const [open, setOpen] = useState(false);
  if (!web) return null;

  if (!web.query_sent || web.results.length === 0) {
    return (
      <div className="cited">
        <span className="cited__count mono">
          {web.query_sent
            ? `searched the web for “${web.query_sent}”, ${web.error ?? "no results"}`
            : `did not search the web: ${web.error ?? "no safe query"}`}
        </span>
      </div>
    );
  }

  return (
    <div className="cited">
      <button
        className="cited__toggle"
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
      >
        <span className={`cited__caret ${open ? "is-open" : ""}`}>▸</span>
        <span className="cited__count mono">
          searched the web for “{web.query_sent}” · {web.results.length}{" "}
          {web.results.length === 1 ? "result" : "results"}
        </span>
      </button>

      {open && (
        <ol className="cited__list">
          {web.results.map((result, i) => (
            <li key={result.url} className="cited__item">
              <span className="cited__where mono">[{i + 1}]</span>
              <a className="cited__file" href={result.url} target="_blank" rel="noreferrer noopener">
                {result.title || result.url}
              </a>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
