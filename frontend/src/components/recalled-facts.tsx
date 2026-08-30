"use client";

/**
 * What memory contributed to an answer.
 *
 * Collapsed by default. Recall is meant to feel like the assistant simply
 * knowing things, and a panel of citations open on every turn would undo that.
 * But when an answer is wrong, the first question is what the model was told,
 * and this is the only place that can say.
 */

import React, { useState } from "react";
import type { RecalledFact } from "@/lib/api";

const KIND_LABEL: Record<RecalledFact["kind"], string> = {
  relationship: "fact",
  entity: "entity",
  episode: "episode",
};

export function RecalledFacts({ facts }: { facts: RecalledFact[] }) {
  const [open, setOpen] = useState(false);

  if (facts.length === 0) return null;

  const userCount = facts.filter(f => f.scope === "user").length;
  const projectCount = facts.length - userCount;

  return (
    <div className="recall">
      <button
        className="recall__toggle"
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
      >
        <span className={`recall__caret ${open ? "is-open" : ""}`}>▸</span>
        <span className="recall__count mono">
          recalled {facts.length} {facts.length === 1 ? "fact" : "facts"}
        </span>
        <span className="recall__tiers mono">
          {userCount > 0 && <span className="recall__tier recall__tier--user">{userCount} you</span>}
          {projectCount > 0 && (
            <span className="recall__tier recall__tier--project">{projectCount} project</span>
          )}
        </span>
      </button>

      {open && (
        <ul className="recall__list">
          {facts.map(fact => (
            <li key={fact.id} className="recall__item">
              <span className={`recall__badge recall__badge--${fact.scope}`}>{fact.scope}</span>
              <span className="recall__fact">{fact.fact}</span>
              <span className="recall__kind mono">{KIND_LABEL[fact.kind] ?? fact.kind}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
