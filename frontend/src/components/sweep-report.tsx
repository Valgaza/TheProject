"use client";

/**
 * What the maintenance pass found, before you let it act.
 *
 * Shown as a diff rather than a count. The sweep marks instead of deleting, so
 * its decisions are reversible — but only if you can see what they were, and a
 * number cannot tell you which fact it touched.
 */

import React from "react";
import { Icon } from "@/components/icons";
import type { MaintenanceResult } from "@/lib/api";

const SECTIONS: {
  key: keyof MaintenanceResult["reports"][number];
  label: string;
  /** Changes the pass would make, versus things it wants a human to look at. */
  acts: boolean;
}[] = [
  { key: "duplicate_edges", label: "Duplicate facts", acts: true },
  { key: "near_duplicates", label: "Restated facts", acts: true },
  { key: "summaries_rebuilt", label: "Summaries rebuilt", acts: true },
  { key: "episodes_suppressed", label: "Repeated episodes hidden", acts: true },
  { key: "orphans_deleted", label: "Orphans removed", acts: true },
  { key: "conflicts", label: "Needs a human", acts: false },
  { key: "suspect_invalidations", label: "Suspect invalidations", acts: false },
];

export function SweepReportPanel({
  result,
  busy,
  onApply,
  onDismiss,
}: {
  result: MaintenanceResult;
  busy: boolean;
  onApply: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className="sweep">
      <div className="inspector__head">
        <div className="eyebrow">§ Maintenance</div>
        <button className="ctool" onClick={onDismiss} title="Close">
          <Icon name="close" size={14} />
        </button>
      </div>

      <h3 className="inspector__name display">
        {result.clean ? "Nothing to clean." : `${result.total_changes} changes`}
      </h3>
      <p className="sweep__lede">
        {result.clean
          ? "The graph holds no duplicates, no stale summaries and no orphans."
          : result.dry_run
          ? "Nothing has changed yet. Superseded facts are marked, not deleted, so this is reversible."
          : "Applied."}
      </p>

      {result.reports.map(report => (
        <div key={report.group_id} className="sweep__group">
          <div className={`recall__badge recall__badge--${report.scope}`}>
            {report.scope === "user" ? "universal" : "project"}
          </div>
          {SECTIONS.map(({ key, label, acts }) => {
            const items = report[key] as string[];
            if (!items?.length) return null;
            return (
              <div key={key} className="sweep__section">
                <div className={`sweep__label ${acts ? "" : "is-flag"}`}>
                  {label} <span className="mono">{items.length}</span>
                </div>
                <ul className="sweep__items">
                  {items.map((item, i) => (
                    <li key={i}>{item}</li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      ))}

      {result.dry_run && !result.clean && (
        <div className="sweep__actions">
          <button className="btn btn--ghost" onClick={onDismiss} disabled={busy}>
            Cancel
          </button>
          <button className="btn btn--primary" onClick={onApply} disabled={busy}>
            {busy ? "Applying…" : "Apply changes"}
          </button>
        </div>
      )}
    </div>
  );
}
