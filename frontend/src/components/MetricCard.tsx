import { ago, dayClock, SOURCE_LABEL } from "../lib/format";
import type { Metric } from "../lib/types";

const SEV = [
  { bar: "bg-emerald-500", text: "text-emerald-700", label: "Low" },
  { bar: "bg-yellow-400", text: "text-yellow-700", label: "Elevated" },
  { bar: "bg-orange-500", text: "text-orange-700", label: "High" },
  { bar: "bg-red-600", text: "text-red-700", label: "Severe" },
  { bar: "bg-purple-600", text: "text-purple-700", label: "Extreme" },
];

export default function MetricCard({ m, now }: { m: Metric; now: Date }) {
  const sev = SEV[Math.min(4, Math.max(0, m.severity))];
  const value = m.value == null ? "—" : Number.isInteger(m.value) ? String(m.value) : m.value.toFixed(1);
  // Current wind is the NWS forecast for this hour standing in for "now"; its detail line still says so.
  const forNow = m.key === "wind";
  return (
    <article className={`relative overflow-hidden rounded-xl border border-line bg-white p-3 pl-4 shadow-card ${m.available ? "" : "opacity-75"}`}>
      <span className={`absolute inset-y-0 left-0 w-1.5 ${m.available ? sev.bar : "bg-slate-200"}`} aria-hidden />
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-[13px] font-medium text-ink-2">{m.label}</h3>
        {m.layer === "forecast" && m.available && <span className="rounded bg-sky-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-sky-700">{forNow ? "Now" : "Forecast"}</span>}
      </div>
      <div className="mt-1 flex items-baseline gap-1.5">
        <span className="text-2xl font-semibold tabular-nums text-ink">{value}</span>
        {m.value != null && <span className="text-sm text-ink-3">{m.unit}</span>}
        {m.available && <span className={`ml-auto text-sm font-medium ${sev.text}`}>{m.category}</span>}
      </div>
      <p className="mt-1 text-sm leading-snug text-ink-2">{m.advice}</p>
      {m.detail && <p className="mt-1 text-xs text-ink-3">{m.detail}</p>}
      <p className="mt-2 text-[11px] text-ink-3">
        {SOURCE_LABEL[m.source] ?? m.source}
        {m.observed_or_valid_at && m.available && m.key !== "alerts" ? ` · ${m.layer === "forecast" ? `for ${dayClock(m.observed_or_valid_at)}` : `observed ${ago(m.observed_or_valid_at, now)}`}` : ""}
        {m.stale && <span className="ml-1 font-semibold text-amber-700">· stale</span>}
      </p>
    </article>
  );
}
