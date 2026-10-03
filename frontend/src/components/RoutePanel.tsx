import { useState } from "react";
import { clock, duration } from "../lib/format";
import { formatDistance } from "../lib/geo";
import type { Banner } from "../lib/useNavigation";
import type { Progress } from "../lib/nav";
import type { Route } from "../lib/types";

function TurnIcon({ text }: { text: string }) {
  const rot = /U-turn/.test(text) ? 180 : /Turn right/.test(text) ? 90 : /Turn left/.test(text) ? -90
    : /Bear right/.test(text) ? 45 : /Bear left/.test(text) ? -45 : 0;
  if (/^Arrive/.test(text)) return <span className="text-2xl" aria-hidden>⚑</span>;
  return (
    <svg width="34" height="34" viewBox="0 0 24 24" aria-hidden style={{ transform: `rotate(${rot}deg)` }}>
      <path d="M12 3l6 7h-4v11h-4V10H6z" fill="currentColor" />
    </svg>
  );
}

interface Props {
  route: Route;
  progress: Progress | null;
  now: string;
  banner: Banner | null;
  onDismissBanner: () => void;
  replay: boolean;
  handoff: boolean;
  hasBackup: boolean;
  showBackup: boolean;
  onToggleBackup: () => void;
  onCantContinue: () => void;
  onStop: () => void;
  voice: boolean;
  onVoice: (v: boolean) => void;
  rerouting: boolean;
  noRoute: string | null;
  follow: boolean;
  onRecenter: () => void;
}

/** Navigation bottom sheet (spec §7.8). */
export default function RoutePanel(p: Props) {
  const [stepsOpen, setStepsOpen] = useState(false);
  const r = p.route;
  const speed = r.speed_mps ?? r.distance_m / Math.max(1, r.duration_s);
  const remaining = p.progress?.remaining ?? r.distance_m;
  const etaS = remaining / Math.max(0.5, speed);
  const arrive = new Date(Date.parse(p.now) + etaS * 1000).toISOString();
  const slack = r.deadline ? (Date.parse(r.deadline) - Date.parse(arrive)) / 60000 : null;
  const arrived = !!p.progress?.arrived;
  const next = arrived ? null : p.progress?.next;
  const instr = arrived ? `Arrived at ${r.destination.name}` : next?.step.instruction ?? r.steps[0]?.instruction ?? "Follow the route";

  return (
    <div className="pointer-events-auto space-y-2">
      {p.banner && (
        <div role="alert" className={`flex items-start gap-3 rounded-2xl p-3 text-sm font-semibold shadow-sheet ${
          p.banner.kind === "hazard" ? "bg-red-600 text-white" : p.banner.kind === "reroute" ? "bg-blue-700 text-white"
            : p.banner.kind === "arrived" ? "bg-emerald-700 text-white" : "bg-slate-900 text-white"}`}>
          <span className="flex-1">{p.banner.text}</span>
          <button onClick={p.onDismissBanner} className="text-white/80" aria-label="Dismiss">✕</button>
        </div>
      )}
      {!p.follow && (
        <button onClick={p.onRecenter} className="ml-auto block rounded-full bg-white px-4 py-2 text-sm font-semibold text-ink shadow-card ring-1 ring-black/10">
          Recenter
        </button>
      )}
      <section className="rounded-t-3xl bg-white px-4 pb-[max(env(safe-area-inset-bottom),12px)] pt-3 shadow-sheet ring-1 ring-black/5">
        <div className="mx-auto mb-2 h-1 w-10 rounded-full bg-slate-300" />
        {p.noRoute && (
          <div className="mb-3 rounded-xl bg-violet-700 p-3 text-white">
            <p className="font-bold">🛡️ Shelter in place at the highest point you can reach</p>
            <p className="mt-1 text-sm text-white/90">{p.noRoute} Do not walk or drive into water. Share your location with an emergency contact and call 911 if water is rising around you.</p>
          </div>
        )}
        <div className="flex items-center gap-3">
          <div className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl text-white ${arrived ? "bg-emerald-700" : "bg-blue-700"}`}>
            <TurnIcon text={arrived ? "Arrive" : instr} />
          </div>
          <div className="min-w-0">
            <p className="text-lg font-bold leading-tight text-ink">{instr}</p>
            {next && <p className="text-sm text-ink-2">in {formatDistance(next.inMeters)}</p>}
          </div>
        </div>
        {!arrived && <div className="mt-3 grid grid-cols-3 gap-2 rounded-xl bg-slate-50 p-2 text-center">
          <div><div className="text-[11px] uppercase tracking-wide text-ink-3">Left</div><div className="font-semibold tabular-nums">{formatDistance(remaining)}</div></div>
          <div><div className="text-[11px] uppercase tracking-wide text-ink-3">Time</div><div className="font-semibold tabular-nums">{duration(etaS)}</div></div>
          <div><div className="text-[11px] uppercase tracking-wide text-ink-3">Arrive</div><div className="font-semibold tabular-nums">{clock(arrive)}</div></div>
        </div>}
        {r.deadline && !arrived && (
          <p className={`mt-2 rounded-lg px-3 py-2 text-sm font-medium ${slack !== null && slack < 15 ? "bg-amber-100 text-amber-900" : "bg-emerald-50 text-emerald-900"}`}>
            Arrive by ~{clock(r.deadline)}, before the forecast flooding on your route.
            {slack !== null && slack < 15 && " Time is tight — keep moving."}
          </p>
        )}
        <p className="mt-2 text-xs text-ink-3">
          {r.label} → <b className="text-ink-2">{r.destination.name}</b> · {r.destination.label}
          {p.rerouting && <span className="ml-1 animate-pulse font-semibold text-blue-700">Rerouting…</span>}
        </p>

        <div className="mt-3 grid grid-cols-2 gap-2">
          <button onClick={p.onCantContinue} className="min-h-[48px] rounded-xl bg-red-50 px-3 text-sm font-semibold text-red-800 ring-1 ring-red-200">
            I can't continue this way
          </button>
          {p.hasBackup ? (
            <button onClick={p.onToggleBackup} className="min-h-[48px] rounded-xl bg-slate-100 px-3 text-sm font-semibold text-ink">
              {p.showBackup ? "Hide backup route" : "Show backup route"}
            </button>
          ) : <div />}
        </div>
        {p.handoff && !p.replay && r.google_maps_url && (
          <div className="mt-2">
            <div className="grid grid-cols-2 gap-2">
              <a href={r.google_maps_url} target="_blank" rel="noreferrer" className="flex min-h-[48px] items-center justify-center rounded-xl bg-slate-900 text-sm font-semibold text-white">
                Open in Google Maps
              </a>
              {r.apple_maps_url && (
                <a href={r.apple_maps_url} target="_blank" rel="noreferrer" className="flex min-h-[48px] items-center justify-center rounded-xl bg-slate-100 text-sm font-semibold text-ink">
                  Apple Maps
                </a>
              )}
            </div>
            <p className="mt-1.5 text-xs text-amber-800">Google Maps doesn't know about forecast flooding. If it suggests a different road, follow Haven's route.</p>
          </div>
        )}
        <div className="mt-3 flex items-center justify-between text-sm">
          <label className="flex items-center gap-2 text-ink-2">
            <input type="checkbox" checked={p.voice} onChange={(e) => p.onVoice(e.target.checked)} className="h-4 w-4" />
            Voice
          </label>
          <button onClick={() => setStepsOpen(!stepsOpen)} className="text-ink-2 underline-offset-2 hover:underline">{stepsOpen ? "Hide" : "All"} steps</button>
          <button onClick={p.onStop} className="font-semibold text-ink-3 hover:text-ink">End</button>
        </div>
        {stepsOpen && (
          <ol className="mt-2 max-h-48 space-y-1 overflow-auto border-t border-line pt-2 text-sm">
            {r.steps.map((s, i) => (
              <li key={i} className="flex justify-between gap-2"><span>{s.instruction}</span><span className="tabular-nums text-ink-3">{s.distance_m ? formatDistance(s.distance_m) : ""}</span></li>
            ))}
          </ol>
        )}
        {!p.replay && (
          <p className="mt-2 text-[11px] text-ink-3">Location is tracked only while this page is open in the foreground. For backgrounded navigation, use the Google Maps handoff.</p>
        )}
      </section>
    </div>
  );
}
