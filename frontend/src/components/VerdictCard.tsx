import { LEVEL_COLOR, SOURCE_LABEL } from "../lib/format";
import type { Verdict } from "../lib/types";

export default function VerdictCard({ v, busy, onAssumption }: { v: Verdict; busy?: boolean; onAssumption?: (field: string) => void }) {
  const color = LEVEL_COLOR[v.level] ?? "#0f172a";
  return (
    <section className="overflow-hidden rounded-2xl bg-white shadow-card ring-1 ring-black/5" aria-live="polite">
      <div className="px-4 pb-4 pt-3 text-white" style={{ background: color }}>
        <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-white/80">
          <span>Level {v.level} of 5</span>
          {busy && <span className="animate-pulse normal-case tracking-normal">Updating…</span>}
        </div>
        <h2 className="mt-1 flex items-start gap-2 text-[22px] font-bold leading-tight">
          <span aria-hidden>{v.emoji}</span>
          <span>{v.label}</span>
        </h2>
        <p className="mt-2 text-[15px] leading-snug text-white/95">{v.reason}</p>
      </div>
      {v.rephrased && <p className="border-b border-line bg-slate-50 px-4 py-3 text-sm text-ink-2">{v.rephrased}</p>}
      {v.fallback && <p className="border-b border-line bg-violet-50 px-4 py-2.5 text-sm font-medium text-violet-900">{v.fallback}</p>}
      {v.timeline.length > 0 && (
        <div className="px-4 py-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-3">What official sources show</h3>
          <ol className="mt-2 space-y-1.5 border-l-2 border-line pl-3">
            {v.timeline.map((t, i) => (
              <li key={i} className="relative text-sm text-ink-2">
                <span className="absolute -left-[17px] top-1.5 h-2 w-2 rounded-full bg-slate-400" />
                {t}
              </li>
            ))}
          </ol>
        </div>
      )}
      {v.assumptions.length > 0 && (
        <div className="border-t border-line px-4 py-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-3">Assumptions — tap to correct</h3>
          <div className="mt-2 flex flex-wrap gap-2">
            {v.assumptions.map((a) => (
              <button key={a.field} onClick={() => onAssumption?.(a.field)}
                className="rounded-full border border-dashed border-slate-400 bg-slate-50 px-3 py-1.5 text-left text-xs text-ink-2 hover:bg-slate-100">
                {a.text.replace(" Tap to change.", "")}
              </button>
            ))}
          </div>
        </div>
      )}
      <p className="border-t border-line px-4 py-2 text-[11px] text-ink-3">
        Sources: {v.sources.map((s) => SOURCE_LABEL[s] ?? s).join(" · ")}. Haven is decision support; official instructions come first.
      </p>
    </section>
  );
}
