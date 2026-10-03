import { useEffect, useState } from "react";
import Header from "../components/Header";
import Map from "../components/Map";
import TimeSlider from "../components/TimeSlider";
import { api } from "../lib/api";
import { dateTime } from "../lib/format";
import { useStore } from "../lib/store";
import type { ResponderResponse } from "../lib/types";

/** Responder view (spec §12 screen 5): areas cut off from hospitals, ranked. */
export default function Responder() {
  const { meta, state, replay, t, scrubbing } = useStore();
  const [data, setData] = useState<ResponderResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!meta || scrubbing || (replay && !t)) return;
    let live = true;
    setBusy(true);
    api.responder(replay ? t : null).then((d) => { if (live) { setData(d); setErr(null); } })
      .catch((e) => live && setErr(String(e.message ?? e))).finally(() => live && setBusy(false));
    return () => { live = false; };
  }, [meta, replay, t, scrubbing, state?.version]);

  return (
    <div className="flex h-[100dvh] flex-col">
      <Header />
      <div className="relative flex min-h-0 flex-1 flex-col md:flex-row">
        <div className="relative h-[40dvh] shrink-0 md:order-2 md:h-auto md:flex-1">
          <Map meta={meta} state={state} overlay={data?.cut_off_areas ?? null}
            points={data?.hospitals.map((h) => ({ lat: h.lat, lon: h.lon, label: h.name }))} />
        </div>
        <main className="min-h-0 flex-1 overflow-y-auto bg-slate-50 md:order-1 md:w-[460px] md:flex-none md:border-r md:border-line">
          <div className="space-y-4 p-4 pb-40">
            <section>
              <h1 className="text-xl font-bold">Cut off from hospitals</h1>
              <p className="mt-1 text-sm text-ink-2">Census tracts whose roads have no drive path to a hospital once flooded and closed roads are removed.</p>
            </section>
            {err && <p className="rounded-xl bg-red-50 p-3 text-sm text-red-800">{err}</p>}
            {data && (
              <section className="grid grid-cols-2 gap-2">
                <div className="rounded-2xl bg-white p-3 shadow-card ring-1 ring-black/5">
                  <div className="text-xs uppercase tracking-wide text-ink-3">People cut off (est.)</div>
                  <div className="text-3xl font-bold tabular-nums">{data.population.toLocaleString()}</div>
                </div>
                <div className="rounded-2xl bg-white p-3 shadow-card ring-1 ring-black/5">
                  <div className="text-xs uppercase tracking-wide text-ink-3">Tracts affected</div>
                  <div className="text-3xl font-bold tabular-nums">{data.cut_off_areas.features.length}</div>
                </div>
              </section>
            )}
            {busy && !data && <div className="h-40 animate-pulse rounded-2xl bg-slate-200" />}
            {data && data.priority_list.length > 0 && (
              <section className="rounded-2xl bg-white shadow-card ring-1 ring-black/5">
                <h2 className="border-b border-line px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-ink-3">Priority list</h2>
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-ink-3">
                    <tr><th className="px-4 py-2 font-medium">Tract</th><th className="px-2 py-2 text-right font-medium">Cut off</th><th className="px-4 py-2 text-right font-medium">People</th>
                      {data.acs && <th className="px-2 py-2 text-right font-medium">65+</th>}</tr>
                  </thead>
                  <tbody>
                    {data.priority_list.map((r) => (
                      <tr key={r.geoid} className="border-t border-line">
                        <td className="px-4 py-2">{r.name.replace("Census Tract ", "Tract ")}</td>
                        <td className="px-2 py-2 text-right tabular-nums">{Math.round(r.share_cut_off * 100)}%</td>
                        <td className="px-4 py-2 text-right font-semibold tabular-nums">{r.population_cut_off_est.toLocaleString()}</td>
                        {data.acs && <td className="px-2 py-2 text-right tabular-nums">{r.age65_cut_off_est ?? "—"}</td>}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}
            {data && data.priority_list.length === 0 && <p className="rounded-xl bg-emerald-50 p-3 text-sm text-emerald-900">No area is cut off from hospitals in this data.</p>}
            {data?.accuracy && (
              <section className="rounded-2xl bg-white p-4 shadow-card ring-1 ring-black/5">
                <h2 className="text-xs font-semibold uppercase tracking-wide text-ink-3">Accuracy vs NCDOT closures (replay)</h2>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <div><div className="text-2xl font-bold tabular-nums">{Math.round(data.accuracy.recall * 100)}%</div><div className="text-xs text-ink-3">recall · {data.accuracy.closures.filter((c) => c.matched).length} of {data.accuracy.closures_in_corridor} corridor closures flagged</div></div>
                  <div><div className="text-2xl font-bold tabular-nums">{data.accuracy.precision_lower_bound != null ? `≥${Math.round(data.accuracy.precision_lower_bound * 100)}%` : "—"}</div><div className="text-xs text-ink-3">precision (lower bound) · {data.accuracy.flagged_drive_edges} flagged road segments</div></div>
                </div>
                <ul className="mt-3 space-y-1 text-sm">
                  {data.accuracy.closures.map((c, i) => (
                    <li key={i} className="flex gap-2"><span className={c.matched ? "text-emerald-700" : "text-red-700"}>{c.matched ? "✓" : "✗"}</span><span><b>{c.road}</b> <span className="text-ink-3">{c.reason}</span></span></li>
                  ))}
                </ul>
                <p className="mt-2 text-xs text-ink-3">{data.accuracy.note} Scored using the highest observed levels up to {dateTime(data.accuracy.as_of)}.</p>
              </section>
            )}
            {data?.notes.map((n) => <p key={n} className="text-xs text-ink-3">{n}</p>)}
          </div>
        </main>
        {replay && t && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 p-3 md:left-[460px]">
            <div className="pointer-events-auto mx-auto max-w-2xl"><TimeSlider compact /></div>
          </div>
        )}
      </div>
    </div>
  );
}
