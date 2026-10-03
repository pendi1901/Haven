import { CATEGORY_COLOR, CATEGORY_LABEL, dateTime, dayClock } from "../lib/format";
import type { GaugeStatus } from "../lib/types";
import GaugeChart from "./GaugeChart";

export default function GaugePanel({ g, now, onClose }: { g: GaugeStatus; now: string; onClose: () => void }) {
  const next = (["minor", "moderate", "major"] as const)
    .map((c) => ({ c, t: g.time_to_category[c] }))
    .filter((x) => x.t && Date.parse(x.t) > Date.parse(now));
  return (
    <section className="rounded-2xl bg-white p-4 shadow-card ring-1 ring-black/5" aria-label={`Gauge ${g.short_name}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold text-ink">{g.short_name}</h3>
          <p className="text-xs text-ink-3">{g.name} · NOAA {g.lid}{g.usgs_id ? ` · USGS ${g.usgs_id}` : ""}</p>
        </div>
        <button onClick={onClose} className="rounded-lg px-2 py-1 text-sm text-ink-3 hover:bg-slate-100" aria-label="Close gauge">✕</button>
      </div>
      <div className="mt-3 flex items-center gap-3">
        <span className="h-3 w-3 rounded-full ring-2 ring-slate-900/20" style={{ background: g.online ? CATEGORY_COLOR[g.category_now] : "#cbd5e1" }} />
        <span className="text-2xl font-semibold tabular-nums">{g.observed_stage_ft != null ? `${g.observed_stage_ft.toFixed(2)} ft` : "—"}</span>
        <span className="text-sm font-medium text-ink-2">{CATEGORY_LABEL[g.category_now]}</span>
      </div>
      <p className="text-xs text-ink-3">Observed {dateTime(g.observed_at)}{g.record_crest_ft ? ` · record crest ${g.record_crest_ft} ft` : ""}</p>
      {g.note && <p className={`mt-2 rounded-lg px-2.5 py-1.5 text-xs ${g.online ? "bg-slate-50 text-ink-2" : "bg-amber-50 text-amber-900"}`}>{g.note}</p>}
      <div className="mt-3"><GaugeChart g={g} now={now} /></div>
      {g.forecast.length > 0 && (
        <div className="mt-2 space-y-1 text-sm">
          {g.forecast_peak_ft != null && (
            <p><b>Official crest forecast:</b> {g.forecast_peak_ft.toFixed(1)} ft ({CATEGORY_LABEL[g.forecast_peak_category]}) ~{dayClock(g.forecast_peak_at)}</p>
          )}
          {next.map((x) => <p key={x.c} className="text-ink-2">{x.c[0].toUpperCase() + x.c.slice(1)} stage ~{dayClock(x.t)}</p>)}
          <p className="text-[11px] text-ink-3">{g.forecast_source} · issued {dateTime(g.forecast_issued_at)}. Times between official forecast points are interpolated.</p>
        </div>
      )}
    </section>
  );
}
