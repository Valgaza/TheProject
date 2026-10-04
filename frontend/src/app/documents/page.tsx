"use client";

/**
 * The documents that follow you everywhere.
 *
 * Files here sit in the universal tier: any chat can retrieve from them,
 * inside a project or outside one. That is the same rule the knowledge graph
 * applies to facts about you -- what is true of the person holds inside
 * their projects, while what is true of a project stays there.
 *
 * Files belonging to a single project live on that project's own page, not
 * here. Two surfaces rather than one filtered list, because the difference
 * between "everywhere" and "only here" is the thing most worth being
 * unambiguous about.
 */

import React from "react";
import Link from "next/link";
import { DocumentsPanel } from "@/components/documents-panel";

export default function DocumentsPage() {
  return (
    <div className="docs-page">
      <header className="docs-page__head">
        <div className="eyebrow">§ Universal</div>
        <h1 className="docs-page__title">Your documents.</h1>
        <p className="docs-page__lede">
          Anything here can be read by every conversation. For material that
          belongs to one piece of work, add it to that{" "}
          <Link href="/projects">project</Link> instead — it stays inside.
        </p>
      </header>

      <DocumentsPanel />
    </div>
  );
}
