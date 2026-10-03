import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import GaugePanel from "../components/GaugePanel";
import Header from "../components/Header";
import LocationPicker from "../components/LocationPicker";
import Map from "../components/Map";
import MetricCard from "../components/MetricCard";
import TimeSlider from "../components/TimeSlider";
import { CATEGORY_COLOR, CATEGORY_LABEL, dayClock, LEVEL_COLOR } from "../lib/format";
import { useStore } from "../lib/store";
import { api } from "../lib/api";
import type { GaugeStatus, Metric } from "../lib/types";

export default function Dashboard() {
  const { meta, state, stateError, assess, assessing, assessError, location, setLocation, replay, t, metaError } = useStore();
  const [gauge, setGauge] = useState<GaugeStatus | null>(null);
  const [picking, setPicking] = useState(false);
  const [locOpen, setLocOpen] = useState(!location);
  const nav = useNavigate();
  const now = useMemo(() => new Date(state?.t ?? Date.now()), [state?.t]);

  // Crisis mode turns on automatically when the engine says so (spec §1).
  const wasCrisis = useRef<boolean | null>(null);
  useEffect(() => {
    if (!assess) return;
    if (wasCrisis.current === false && assess.crisis) nav("/crisis");
    wasCrisis.current = assess.crisis;
  }, [assess, nav]);

  useEffect(() => {
    if (gauge && state) setGauge(state.gauges.find((g) => g.lid === gauge.lid) ?? null);
  }, [state]); // eslint-disable-line react-hooks/exhaustive-deps

  // Conditions at the user's spot; region center until a location is set.
  const [spot, setSpot] = useState<Metric[] | null>(null);
  useEffect(() => {
    if (!location || !state) return setSpot(null);
    let live = true;
    api.metrics(location, replay ? state.t : null).then((m) => live && setSpot(m)).catch(() => live && setSpot(null));
    return () => { live = false; };
  }, [location, state?.t, state?.version, replay]); // eslint-disable-line react-hooks/exhaustive-deps
  const personal = spot ?? state?.metrics_region ?? [];

  const headsUp = useMemo(() => {
    if (!state) return [];
    const items: { key: string; text: string; tone: "watch" | "forecast" }[] = [];
    for (const z of state.zones) {
      if (z.hazard === "nws_alert" && !z.is_warning) items.push({ key: z.id, text: `${z.event} in effect (NWS)`, tone: "watch" });
    }
    for (const g of state.gauges) {
      for (const c of ["minor", "moderate", "major"] as const) {
        const when = g.time_to_category[c];
        if (when && Date.parse(when) > Date.parse(state.t) && (["none", "action"].includes(g.category_now) || c !== "minor")) {
          items.push({ key: g.lid + c, text: `NOAA forecasts the ${g.short_name} to reach ${c} flood stage ${dayClock(when)}.`, tone: "forecast" });
          break;
        }
      }
    }
    for (const z of state.zones) if (z.hazard === "hurricane") items.push({ key: z.id, text: z.reason, tone: "forecast" });
    return items;
  }, [state]);

  if (metaError) return <Fatal msg={metaError} />;
  const v = assess?.verdict;

  return (
    <div className="flex h-[100dvh] flex-col">
      <Header onLocation={() => setLocOpen(true)} />
      <div className="relative flex min-h-0 flex-1 flex-col md:flex-row">
        <div className="relative h-[42dvh] shrink-0 md:order-2 md:h-auto md:flex-1">
          <Map meta={meta} state={state} location={location} picking={picking} fit={picking ? undefined : "location"}
            onPick={(l) => { setLocation({ ...l, label: "Pinned location", source: "map" }); setPicking(false); setLocOpen(false); }}
            onGauge={setGauge} liveGeolocate={false} />
          {picking && <div className="pointer-events-none absolute inset-x-0 top-3 mx-auto w-fit rounded-full bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sheet">Tap the map to set your location</div>}
          <Legend />
        </div>
        <aside className="min-h-0 flex-1 overflow-y-auto bg-slate-50 md:order-1 md:w-[440px] md:flex-none md:border-r md:border-line">
          <div className="space-y-4 p-4 pb-40">
            {(locOpen || !location) && (
              <section className="rounded-2xl bg-white p-4 shadow-card ring-1 ring-black/5">
                <h2 className="font-semibold">Where are you?</h2>
                <p className="mb-3 text-sm text-ink-2">Haven evaluates risk for one spot. Nothing is stored on the server.</p>
                <LocationPicker compact onPick={(l) => { setLocation(l); setLocOpen(false); }} onMapPick={() => setPicking(true)} />
              </section>
            )}

            {location && assessError && (
              <div className="rounded-2xl bg-red-50 p-4 text-sm text-red-900 ring-1 ring-red-200">
                <p className="font-semibold">Haven couldn't assess {location.label}.</p>
                <p className="mt-1">{assessError}</p>
                <button onClick={() => setLocOpen(true)} className="mt-2 font-semibold underline">Choose another location</button>
              </div>
            )}
            {location && v && !assessError && (
              <Link to="/crisis" className="block overflow-hidden rounded-2xl text-white shadow-card" style={{ background: LEVEL_COLOR[v.level] }}>
                <div className="p-4">
                  <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-white/80">
                    <span>{assess.crisis ? "Crisis mode" : "For " + location.label}</span>
                    {assessing && <span className="animate-pulse normal-case tracking-normal">Updating…</span>}
                  </div>
                  <div className="mt-1 text-xl font-bold leading-tight"><span aria-hidden>{v.emoji}</span> {v.label}</div>
                  <p className="mt-1 text-sm text-white/90">{v.reason}</p>
                  <div className="mt-3 text-sm font-semibold underline underline-offset-4">{assess.crisis ? "See what to do now →" : "Details →"}</div>
                </div>
              </Link>
            )}

            {headsUp.length > 0 && (
              <section>
                <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-3">Heads-up from official forecasts</h2>
                <ul className="space-y-2">
                  {headsUp.map((h) => (
                    <li key={h.key} className={`rounded-xl border px-3 py-2.5 text-sm ${h.tone === "watch" ? "border-amber-200 bg-amber-50 text-amber-950" : "border-sky-200 bg-sky-50 text-sky-950"}`}>{h.text}</li>
                  ))}
                </ul>
              </section>
            )}

            {gauge && state && <GaugePanel g={gauge} now={state.t} onClose={() => setGauge(null)} />}

            <section>
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-3">River gauges</h2>
              <div className="grid gap-2">
                {(state?.gauges ?? []).map((g) => (
                  <button key={g.lid} onClick={() => setGauge(g)} className="flex items-center gap-3 rounded-xl bg-white px-3 py-2.5 text-left shadow-card ring-1 ring-black/5 hover:ring-slate-300">
                    <span className="h-3 w-3 shrink-0 rounded-full" style={{ background: g.online ? CATEGORY_COLOR[g.category_now] : "#cbd5e1" }} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-semibold">{g.short_name}</span>
                      <span className="block text-xs text-ink-3">{g.online ? CATEGORY_LABEL[g.category_now] : "Offline · last reading shown"}{g.forecast_peak_ft != null ? ` · crest ${g.forecast_peak_ft.toFixed(1)} ft forecast` : ""}</span>
                    </span>
                    <span className="text-lg font-semibold tabular-nums">{g.observed_stage_ft?.toFixed(1) ?? "—"}<span className="text-xs font-normal text-ink-3"> ft</span></span>
                  </button>
                ))}
              </div>
            </section>

            <section>
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-3">Conditions{location ? "" : " (region)"}</h2>
              {stateError && <p className="mb-2 rounded-lg bg-red-50 p-2 text-sm text-red-800">Couldn't load data: {stateError}</p>}
              <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-1 lg:grid-cols-1">
                {(personal ?? []).map((m) => <MetricCard key={m.key} m={m} now={now} />)}
              </div>
            </section>

            {state && (
              <p className="text-[11px] leading-relaxed text-ink-3">
                {replay ? "Replay uses archived official data only as it was known at the selected time: USGS gage height, NWS river forecasts (LMRFC), NWS warnings and NHC advisories. " : ""}
                Road impacts and flood extent are Haven's overlay of official gauge levels and forecasts on USGS lidar terrain, not an official inundation map.
                {state.counts.flooded + state.counts.forecast_flooded > 0 && ` Now: ${state.counts.flooded} road segments flooded, ${state.counts.forecast_flooded} forecast to flood.`}
              </p>
            )}
          </div>
        </aside>
        {replay && t && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 p-3 md:left-[440px]">
            <div className="pointer-events-auto mx-auto max-w-2xl"><TimeSlider /></div>
          </div>
        )}
      </div>
    </div>
  );
}

function Legend() {
  const [open, setOpen] = useState(false);
  return (
    <div className="absolute bottom-24 right-2 z-[1] md:bottom-28">
      <button onClick={() => setOpen(!open)} className="rounded-lg bg-white/95 px-2.5 py-1.5 text-xs font-semibold shadow-card ring-1 ring-black/10">{open ? "Hide legend" : "Legend"}</button>
      {open && (
        <div className="mt-1 w-56 rounded-xl bg-white/95 p-3 text-xs shadow-card ring-1 ring-black/10">
          {[
            ["#dc2626", "Flooded road now", "solid"], ["#f59e0b", "At-risk road now", "solid"], ["#7f1d1d", "Closed road", "dash"],
            ["#dc2626", "Forecast to flood (NOAA forecast)", "dash"], ["#1d4ed8", "Your route", "solid"], ["#334155", "Backup route", "dash"],
          ].map(([c, l, s]) => (
            <div key={l} className="flex items-center gap-2 py-0.5">
              <svg width="22" height="6"><line x1="0" x2="22" y1="3" y2="3" stroke={c} strokeWidth="3" strokeDasharray={s === "dash" ? "4 3" : undefined} /></svg>{l}
            </div>
          ))}
          <div className="flex items-center gap-2 py-0.5"><span className="h-3 w-5 rounded-sm bg-blue-700/40" />Estimated flooded area now</div>
          <div className="flex items-center gap-2 py-0.5"><span className="h-3 w-5 rounded-sm border border-dashed border-sky-600 bg-sky-300/30" />Area at forecast crest</div>
          <div className="mt-1 border-t border-line pt-1 text-ink-3">Gauge colors: NWS flood categories</div>
          <div className="flex gap-1.5 pt-1">
            {Object.entries(CATEGORY_COLOR).map(([k, c]) => <span key={k} className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full" style={{ background: c }} />{k}</span>)}
          </div>
        </div>
      )}
    </div>
  );
}

export function Fatal({ msg }: { msg: string }) {
  return (
    <div className="flex h-[100dvh] items-center justify-center p-6 text-center">
      <div>
        <h1 className="text-xl font-bold">Haven can't reach its server</h1>
        <p className="mt-2 text-ink-2">{msg}</p>
        <p className="mt-4 text-sm text-ink-3">If you are in danger, call 911 and follow instructions from local officials and weather.gov.</p>
      </div>
    </div>
  );
}
