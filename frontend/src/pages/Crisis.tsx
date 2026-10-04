import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import AlertBox from "../components/AlertBox";
import CheckInSheet from "../components/CheckIn";
import GaugePanel from "../components/GaugePanel";
import Header from "../components/Header";
import Map from "../components/Map";
import RoutePanel from "../components/RoutePanel";
import TimeSlider from "../components/TimeSlider";
import VerdictCard from "../components/VerdictCard";
import { clock, duration } from "../lib/format";
import { formatDistance } from "../lib/geo";
import { storage } from "../lib/storage";
import { useStore } from "../lib/store";
import { useNavigation } from "../lib/useNavigation";
import type { Companions, GaugeStatus, Route } from "../lib/types";
import { Fatal } from "./Dashboard";

const DEFAULT_COMPANIONS: Companions = { count: 1, kids: false, older_adults: false, limited_mobility: true, pets: false };

export default function Crisis() {
  const { meta, metaError, state, assess, assessing, assessError, location, setLocation, answer, checkin, replay, t, refreshAssess } = useStore();
  const [skipped, setSkipped] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<string | null>(null);
  const [nav, setNav] = useState<null | { route: Route; backup: Route | null; simulate: boolean }>(null);
  const [showBackup, setShowBackup] = useState(false);
  const [done, setDone] = useState<Set<number>>(new Set());
  const [gauge, setGauge] = useState<GaugeStatus | null>(null);
  const v = assess?.verdict;

  useEffect(() => setDone(new Set()), [v?.label]);
  useEffect(() => { if (gauge && state) setGauge(state.gauges.find((g) => g.lid === gauge.lid) ?? null); }, [state]); // eslint-disable-line react-hooks/exhaustive-deps

  const questions = useMemo(() => (assess?.questions ?? []).filter((q) => !skipped.has(q.id) || q.field === focus), [assess, skipped, focus]);

  if (metaError) return <Fatal msg={metaError} />;

  if (nav) {
    return (
      <NavigationView
        key={nav.route.edge_ids.join(",").slice(0, 64)}
        initial={nav.route} backup={nav.backup} simulate={nav.simulate}
        companions={checkin?.companions ?? DEFAULT_COMPANIONS} needsHelp={!!checkin?.needs_help}
        purpose={assess?.hazard === "smoke" || assess?.hazard === "heat" ? "center" : assess?.hazard === "wildfire" ? "fire" : "flood"}
        onExit={() => { setNav(null); storage.saveActiveRoute(null); refreshAssess(); }}
      />
    );
  }

  const route = v?.route ?? null;
  const askLocation = !replay && location?.source !== "gps" && assess?.crisis;

  return (
    <div className="flex h-[100dvh] flex-col">
      <Header />
      <div className="relative flex min-h-0 flex-1 flex-col md:flex-row">
        <div className="relative h-[34dvh] shrink-0 md:order-2 md:h-auto md:flex-1">
          <Map meta={meta} state={state} location={location} route={route} backup={v?.backup_route} showBackup={showBackup}
            fit={route ? "route" : "location"} onGauge={setGauge} />
        </div>
        <main className="min-h-0 flex-1 overflow-y-auto bg-slate-50 md:order-1 md:w-[460px] md:flex-none md:border-r md:border-line">
          <div className="space-y-4 p-4 pb-40">
            {!location && (
              <div className="rounded-2xl bg-white p-4 shadow-card">
                <p className="font-semibold">Set your location first.</p>
                <Link to="/" className="mt-2 inline-block text-sm font-semibold text-blue-700">Go to Today →</Link>
              </div>
            )}
            {askLocation && (
              <div className="flex items-center gap-3 rounded-2xl bg-blue-50 p-3 ring-1 ring-blue-200">
                <p className="flex-1 text-sm text-blue-950">Share your exact location so Haven can check flooding where you are and route you.</p>
                <button className="rounded-xl bg-blue-700 px-3 py-2 text-sm font-semibold text-white" onClick={() =>
                  navigator.geolocation?.getCurrentPosition((p) => setLocation({ lat: p.coords.latitude, lon: p.coords.longitude, label: "Current location", source: "gps" }))}>
                  Share
                </button>
              </div>
            )}
            {assessError && <p className="rounded-xl bg-red-50 p-3 text-sm text-red-800">{assessError}</p>}

            {v && <AlertBox alerts={v.alerts} />}
            {v && (
              <VerdictCard v={v} busy={assessing} onAssumption={(f) => {
                setFocus(f === "building_stories" ? "floor" : f);
                setSkipped((s) => { const n = new Set(s); n.delete(f); n.delete("floor"); n.delete("building_stories"); return n; });
              }} />
            )}
            {!assess && location && <div className="h-40 animate-pulse rounded-2xl bg-slate-200" />}

            {assess?.crisis && questions.length > 0 && (
              <CheckInSheet questions={questions} focus={focus} skipped={skipped}
                onAnswer={(patch) => { answer(patch); setFocus(null); }}
                onSkip={(id) => { setSkipped((s) => new Set(s).add(id)); setFocus(null); }} />
            )}

            {route && v && (
              <section className="rounded-2xl bg-white p-4 shadow-card ring-1 ring-black/5">
                <div className="text-xs font-semibold uppercase tracking-wide text-ink-3">{route.label}</div>
                <h3 className="mt-1 text-lg font-semibold leading-snug">{route.destination.name}</h3>
                <p className="text-sm text-ink-2">{route.destination.label}{route.destination.address ? ` · ${route.destination.address}` : ""}</p>
                <dl className="mt-3 grid grid-cols-3 gap-2 text-center">
                  <div className="rounded-xl bg-slate-50 p-2"><dt className="text-[11px] uppercase text-ink-3">{route.mode === "walk" ? "Walk" : "Drive"}</dt><dd className="font-semibold tabular-nums">{formatDistance(route.distance_m)}</dd></div>
                  <div className="rounded-xl bg-slate-50 p-2"><dt className="text-[11px] uppercase text-ink-3">Takes</dt><dd className="font-semibold tabular-nums">{duration(route.duration_s)}</dd></div>
                  <div className="rounded-xl bg-slate-50 p-2"><dt className="text-[11px] uppercase text-ink-3">Arrive</dt><dd className="font-semibold tabular-nums">{clock(route.arrive_at)}</dd></div>
                </dl>
                {route.deadline && (
                  <p className={`mt-2 rounded-lg px-3 py-2 text-sm ${route.slack_min !== null && route.slack_min < 15 ? "bg-amber-100 text-amber-900" : "bg-emerald-50 text-emerald-900"}`}>
                    Arrive by ~{clock(route.deadline)}, before the forecast flooding on this route.
                  </p>
                )}
                {route.destination.margin_m != null && (
                  <p className="mt-2 text-xs text-ink-3">Ground there is ~{route.destination.margin_m.toFixed(1)} m above the forecast peak water level nearby.</p>
                )}
                {route.notes.map((n) => <p key={n} className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">{n}</p>)}
                <div className="mt-3 grid gap-2">
                  <button onClick={() => { storage.saveActiveRoute(route); setNav({ route, backup: v.backup_route, simulate: replay }); }}
                    className="min-h-[52px] rounded-xl bg-blue-700 text-base font-semibold text-white">
                    {replay ? `Simulate ${route.mode === "walk" ? "walk" : "drive"} along this route` : "Start navigation"}
                  </button>
                  {v.backup_route && (
                    <button onClick={() => setShowBackup(!showBackup)} className="min-h-[44px] rounded-xl bg-slate-100 text-sm font-semibold text-ink">
                      {showBackup ? "Hide" : "Show"} backup: {v.backup_route.destination.name} · {duration(v.backup_route.duration_s)}
                    </button>
                  )}
                </div>
              </section>
            )}

            {v && v.steps.length > 0 && (
              <section className="rounded-2xl bg-white p-4 shadow-card ring-1 ring-black/5">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-3">Steps</h3>
                <ul className="mt-2 space-y-1">
                  {v.steps.map((s, i) => (
                    <li key={i}>
                      <label className="flex cursor-pointer items-start gap-3 rounded-lg p-1.5 hover:bg-slate-50">
                        <input type="checkbox" className="mt-1 h-5 w-5 shrink-0 accent-slate-900" checked={done.has(i)}
                          onChange={() => setDone((d) => { const n = new Set(d); n.has(i) ? n.delete(i) : n.add(i); return n; })} />
                        <span className={`text-[15px] leading-snug ${done.has(i) ? "text-ink-3 line-through" : "text-ink"}`}>{s}</span>
                      </label>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {gauge && state && <GaugePanel g={gauge} now={state.t} onClose={() => setGauge(null)} />}

            {assess && assess.triggers.length > 0 && (
              <details className="rounded-2xl bg-white p-4 text-sm shadow-card ring-1 ring-black/5">
                <summary className="cursor-pointer font-semibold">Why Haven switched to crisis mode</summary>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-ink-2">{assess.triggers.map((x) => <li key={x}>{x}</li>)}</ul>
              </details>
            )}
            <p className="text-xs text-ink-3">If you are in immediate danger, call 911. Haven is decision support built on official data; it never replaces instructions from emergency officials.</p>
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

const RATES = [0, 15, 30, 60]; // 0 = paused

function NavigationView({ initial, backup, simulate, companions, needsHelp, purpose, onExit }: {
  initial: Route; backup: Route | null; simulate: boolean; companions: Companions; needsHelp: boolean;
  purpose: "flood" | "center" | "fire"; onExit: () => void;
}) {
  const { meta, state, replay, t, setT } = useStore();
  const [rate, setRate] = useState(30);
  const [follow, setFollow] = useState(true);
  const [showBackup, setShowBackup] = useState(false);
  const n = useNavigation({ initial, backup, companions, needsHelp, simulate, rate, purpose });
  const now = state?.t ?? new Date().toISOString();

  return (
    <div className="fixed inset-0 flex flex-col bg-white">
      <div className="relative flex-1">
        <Map meta={meta} state={state} route={n.route} backup={backup} showBackup={showBackup} traveled={n.progress?.along}
          user={simulate && n.fix ? { ...n.fix } : null} follow={follow} onUserPan={() => setFollow(false)}
          fit={n.fix ? undefined : "route"} liveGeolocate={!simulate} />
        <div className="absolute left-3 right-14 top-3 flex items-center gap-2">
          <button onClick={onExit} className="rounded-full bg-white px-3 py-2 text-sm font-semibold shadow-card ring-1 ring-black/10">← Back</button>
          {simulate && (
            <div className="flex items-center gap-1 rounded-full bg-slate-900 px-2 py-1 text-xs font-semibold text-white">
              <span className="px-1">{n.noRoute ? "Stopped: no open route" : `Simulated ${n.route.mode}`}</span>
              {!n.noRoute && RATES.map((r) => (
                <button key={r} onClick={() => { setRate(r); n.setRate(r); }} aria-label={r ? `${r} times speed` : "Pause"}
                  className={`rounded-full px-2 py-1 ${rate === r ? "bg-white text-slate-900" : ""}`}>{r ? `${r}×` : "❚❚"}</button>
              ))}
              {!n.noRoute && <button onClick={n.wander} className="ml-1 rounded-full bg-white/15 px-2 py-1" title="Drift ~90 m off the route to test off-route detection">Wander off</button>}
              <button onClick={() => t && setT(new Date(Date.parse(t) + 3600_000).toISOString())} className="rounded-full bg-white/15 px-2 py-1"
                title="Advance the replay clock 1 hour: newer gauge readings and forecasts arrive and the route is re-checked">+1 h</button>
            </div>
          )}
          {replay && <span className="ml-auto rounded-full bg-white/90 px-2.5 py-1 text-xs font-semibold tabular-nums shadow-card">{clock(now)}</span>}
        </div>
      </div>
      <div className="pointer-events-none absolute inset-x-0 bottom-0 mx-auto max-w-xl px-2">
        <RoutePanel route={n.route} progress={n.progress} now={now} banner={n.banner} onDismissBanner={() => n.setBanner(null)}
          replay={replay} handoff={!!meta?.features.handoff} hasBackup={!!backup} showBackup={showBackup}
          onToggleBackup={() => setShowBackup(!showBackup)} onCantContinue={n.cantContinue} onStop={onExit}
          voice={n.voice} onVoice={n.setVoice} rerouting={n.rerouting} noRoute={n.noRoute} follow={follow} onRecenter={() => setFollow(true)} />
      </div>
    </div>
  );
}
