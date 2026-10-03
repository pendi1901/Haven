import { useState } from "react";
import { dateTime } from "../lib/format";
import type { HazardZone } from "../lib/types";

/** Official NWS alert text, verbatim, always shown first (spec §14). */
export default function AlertBox({ alerts }: { alerts: HazardZone[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!alerts.length) return null;
  const sorted = [...alerts].sort((a, b) => b.severity - a.severity);
  return (
    <section aria-label="Official alerts" className="space-y-2">
      {sorted.map((a) => {
        const warn = a.is_warning;
        const expanded = open === a.id;
        return (
          <article key={a.id} className={`rounded-xl border ${warn ? "border-red-300 bg-red-50" : "border-amber-300 bg-amber-50"}`}>
            <button className="flex w-full items-start gap-3 p-3 text-left" onClick={() => setOpen(expanded ? null : a.id)} aria-expanded={expanded}>
              <span className={`mt-0.5 rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${warn ? "bg-red-600 text-white" : "bg-amber-500 text-white"}`}>
                NWS
              </span>
              <span className="flex-1">
                <span className={`block font-semibold ${warn ? "text-red-900" : "text-amber-900"}`}>{a.event}</span>
                {a.headline && <span className="block text-sm text-ink-2">{a.headline}</span>}
              </span>
              <span className="text-xs text-ink-3">{expanded ? "Hide" : "Read"}</span>
            </button>
            {expanded && (
              <div className="border-t border-black/5 px-3 pb-3 pt-2 text-sm text-ink-2">
                {a.instruction && (
                  <>
                    <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-3">What the NWS says to do</h4>
                    <p className="mb-3 whitespace-pre-line font-medium text-ink">{a.instruction}</p>
                  </>
                )}
                {a.description && <pre className="max-h-72 overflow-auto whitespace-pre-wrap font-sans text-[13px] leading-snug">{a.description}</pre>}
                <p className="mt-2 text-[11px] text-ink-3">
                  Issued by the National Weather Service{a.archived ? " · archived product" : ""} · from {dateTime(a.valid_from)}
                  {a.valid_to ? ` to ${dateTime(a.valid_to)}` : ""}
                </p>
              </div>
            )}
          </article>
        );
      })}
    </section>
  );
}
