"use client";

/**
 * Which passages of which documents informed an answer.
 *
 * Deliberately the same shape as RecalledFacts, and collapsed for the same
 * reason: retrieval should feel like the assistant having read your files,
 * not like a search engine handing back results. But when an answer is
 * wrong, what it was given is the only thing worth looking at, and the
 * score is what explains a citation that looks unrelated.
 *
 * The API has returned these since RAG landed. Nothing rendered them until
 * documents had owners, because a citation you cannot trace to a file in
 * your own list is not provenance, it is trivia.
 */

import React, { useState } from "react";
import type { RagSource } from "@/lib/api";

/** "Introduction > How RAG Works > Retrieval" -> "Retrieval" */
function leaf(headingPath: string | undefined): string {
  if (!headingPath) return "";
  const parts = headingPath.split(">").map(p => p.trim()).filter(Boolean);
  return parts[parts.length - 1] ?? "";
}

function locate(source: RagSource): string {
  if (source.page_number !== null && source.page_number !== undefined) {
    return `p. ${source.page_number}`;
  }
  if (source.chunk_index !== null && source.chunk_index !== undefined) {
    return `passage ${source.chunk_index}`;
  }
  return "";
}

export function CitedSources({ sources }: { sources: RagSource[] }) {
  const [open, setOpen] = useState(false);

  if (sources.length === 0) return null;

  // One document can contribute several passages. Counting files rather than
  // chunks is what a person means by "how much did it read".
  const files = new Set(sources.map(s => s.file_name)).size;

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
          read {sources.length} {sources.length === 1 ? "passage" : "passages"}
          {files > 1 ? ` from ${files} documents` : ""}
        </span>
      </button>

      {open && (
        <ul className="cited__list">
          {sources.map((source, i) => {
            const where = locate(source);
            const heading = leaf(source.heading_path);
            return (
              <li key={`${source.file_name}-${i}`} className="cited__item">
                <span className="cited__file">{source.file_name}</span>
                {heading && <span className="cited__heading">{heading}</span>}
                {where && <span className="cited__where mono">{where}</span>}
                {typeof source.rerank_score === "number" && (
                  <span
                    className="cited__score mono"
                    title="How closely this passage matched your question"
                  >
                    {Math.round(source.rerank_score * 100)}%
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
