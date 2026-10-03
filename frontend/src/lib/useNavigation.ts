import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { dayClock } from "./format";
import { distanceToLine, type XY } from "./geo";
import { GeolocationSource, HAZARD_ALERT_M, OffRouteDetector, RouteTracker, SimulatedSource, type PositionFix, type PositionSource, type Progress } from "./nav";
import { storage } from "./storage";
import { useStore } from "./store";
import type { Companions, Route } from "./types";

export interface Banner { kind: "hazard" | "reroute" | "arrived" | "error" | "info"; text: string; id: number }

interface Opts {
  initial: Route;
  backup: Route | null;
  companions: Companions | null;
  needsHelp: boolean;
  simulate: boolean;
  rate: number;
}

/** Live navigation (spec §7.8). GPS and the replay simulator feed the same
 * handler, so every behavior below is exercised identically in both modes. */
export function useNavigation(o: Opts) {
  const { t, setT, replay, state } = useStore();
  const [route, setRoute] = useState<Route>(o.initial);
  const [fix, setFix] = useState<PositionFix | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [banner, setBanner] = useState<Banner | null>(null);
  const [rerouting, setRerouting] = useState(false);
  const [voice, setVoice] = useState(false);
  const [noRoute, setNoRoute] = useState<string | null>(null);
  const tracker = useMemo(() => new RouteTracker(route), [route]);
  const detector = useRef(new OffRouteDetector());
  const warned = useRef(new Set<string>());
  const blocked = useRef<string[]>([]);
  const lastSpoken = useRef<string>("");
  const fixRef = useRef<PositionFix | null>(null);
  const tRef = useRef(t);
  tRef.current = t;
  const simAccum = useRef(0);
  const sourceRef = useRef<PositionSource | null>(null);
  const routeRef = useRef(route);
  routeRef.current = route;
  const stateRef = useRef(state);
  stateRef.current = state;

  const say = useCallback((text: string) => {
    if (!voice || !("speechSynthesis" in window)) return;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
  }, [voice]);

  const lastImportant = useRef(0);
  const show = useCallback((kind: Banner["kind"], text: string) => {
    // Reroute / no-route messages outrank proximity alerts for a few seconds.
    if (kind === "hazard" && Date.now() - lastImportant.current < 8000) return;
    if (kind === "reroute" || kind === "error") lastImportant.current = Date.now();
    setBanner({ kind, text, id: Date.now() });
    if (kind === "hazard" || kind === "reroute") {
      try { navigator.vibrate?.([200, 100, 200]); } catch { /* unsupported */ }
    }
  }, []);

  const reroute = useCallback(async (why: string, from?: PositionFix | null) => {
    const p = from ?? fixRef.current;
    if (!p) return;
    setRerouting(true);
    try {
      const rr = await api.route({
        lat: p.lat, lon: p.lon, mode: routeRef.current.mode, t: replay ? tRef.current : null,
        companions: o.companions, blocked: blocked.current, needs_help: o.needsHelp,
      });
      if (rr.route) {
        setRoute(rr.route);
        storage.saveActiveRoute(rr.route);
        detector.current.reset();
        setNoRoute(null);
        show("reroute", why);
        say(why);
      } else {
        setNoRoute(rr.message ?? "No open route found in this data.");
        show("error", `${rr.message ?? "No open route found in this data."} Go to the highest point you can reach and call 911 if you are in danger.`);
      }
    } catch (e) {
      show("error", `Couldn't reach Haven to reroute (${(e as Error).message}). Your last route is saved on this device.`);
    } finally {
      setRerouting(false);
    }
  }, [o.companions, o.needsHelp, replay, say, show]);

  const onFix = useCallback((f: PositionFix) => {
    fixRef.current = f;
    setFix(f);
    const pr = tracker.progress([f.lon, f.lat]);
    setProgress(pr);
    if (pr.arrived) {
      const d = routeRef.current.destination;
      show("arrived", `You've arrived at ${d.name}. ${d.kind === "open_shelter" ? "Check in with shelter staff. " : d.kind === "high_ground" ? "Stay above the water and away from creeks. " : "Check whether it's open; if not, stay on this higher ground. "}Keep your phone charged and watch for updates.`);
      sourceRef.current?.stop();
      return;
    }
    if (detector.current.update(pr.offBy)) {
      void reroute("You left the route. Here is a new least risky route from where you are.", f);
      return;
    }
    // Hazard proximity: any flooded / at-risk segment within 200 m (on or off route).
    const roads = stateRef.current?.roads.features ?? [];
    for (const r of roads) {
      if (r.properties.status !== "flooded" && r.properties.status !== "at_risk") continue;
      if (warned.current.has(r.properties.id)) continue;
      const c = r.geometry.coordinates as XY[];
      if (Math.abs(c[0][1] - f.lat) > 0.003 && Math.abs(c[c.length - 1][1] - f.lat) > 0.003) continue;
      if (distanceToLine([f.lon, f.lat], c) <= HAZARD_ALERT_M) {
        warned.current.add(r.properties.id);
        const kind = r.properties.status === "flooded" ? "Flooded" : "At-risk";
        const what = r.properties.path ? "path" : "road";
        const msg = r.properties.name
          ? `${kind} ${what} ahead on ${r.properties.name}. Do not enter the water.`
          : `${kind} ${what} nearby. Do not enter the water.`;
        show("hazard", msg);
        say(msg);
        break;
      }
    }
    if (pr.next && pr.next.step.instruction !== lastSpoken.current && pr.next.inMeters < 120) {
      lastSpoken.current = pr.next.step.instruction;
      say(pr.next.text);
    }
  }, [tracker, reroute, say, show]);

  // Position source lifecycle.
  useEffect(() => {
    let src: PositionSource;
    if (o.simulate) {
      const speed = route.speed_mps ?? route.distance_m / Math.max(1, route.duration_s);
      src = new SimulatedSource(route.geometry.coordinates as XY[], speed, o.rate, (simDt) => {
        // Advance the replay clock with simulated travel time (15-minute steps).
        simAccum.current += simDt;
        if (simAccum.current >= 900 && tRef.current) {
          const steps = Math.floor(simAccum.current / 900);
          simAccum.current -= steps * 900;
          setT(new Date(Date.parse(tRef.current) + steps * 900_000).toISOString());
        }
      });
    } else {
      src = new GeolocationSource();
    }
    sourceRef.current = src;
    src.start(onFix, (e) => show("error", e));
    return () => src.stop();
    // Restart the source when the route geometry changes (reroute).
    // Speed changes go through setRate on the live source (no restart, no jump back).
  }, [route, o.simulate]); // eslint-disable-line react-hooks/exhaustive-deps

  // Re-plan on new data (spec §7.7 step 8): every replay tick / live update.
  useEffect(() => {
    if (!state || !progress || progress.arrived) return;
    const speed = route.speed_mps ?? route.distance_m / Math.max(1, route.duration_s);
    const arrive = new Date(Date.parse(state.t) + (progress.remaining / Math.max(0.5, speed)) * 1000).toISOString();
    const remainingEdges = route.edge_ids.filter((_, i) => (route.edge_starts_m?.[i + 1] ?? Infinity) > progress.along);
    api.checkRoute({ mode: route.mode, edge_ids: remainingEdges, arrive_at: arrive, t: replay ? state.t : null }).then((c) => {
      if (c.ok) return;
      const p = c.problems[0];
      const what = p.status !== "dry" ? `is ${p.status.replace("_", " ")} now` : `is now expected to flood around ${dayClock(p.first_flood)}`;
      void reroute(`Route changed: ${p.name ?? "a road on your route"} ${what}. Use the new route.`);
    }).catch(() => undefined);
  }, [state?.t, state?.version]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the screen on while navigating.
  useEffect(() => {
    let lock: WakeLockSentinel | null = null;
    const nav = navigator as Navigator & { wakeLock?: { request: (t: "screen") => Promise<WakeLockSentinel> } };
    nav.wakeLock?.request("screen").then((l) => (lock = l)).catch(() => undefined);
    return () => { lock?.release().catch(() => undefined); };
  }, []);

  const cantContinue = useCallback(() => {
    if (!progress) return;
    const starts = route.edge_starts_m ?? [];
    let i = 0;
    while (i < starts.length - 1 && starts[i + 1] <= progress.along) i++;
    const ids = route.edge_ids.slice(i, i + 2);
    blocked.current = Array.from(new Set([...blocked.current, ...ids]));
    void reroute("Marked that road as blocked. Here is a new route.");
  }, [progress, route, reroute]);

  return { route, fix, progress, banner, setBanner, rerouting, voice, setVoice, cantContinue, noRoute,
    setRate: (r: number) => (sourceRef.current as SimulatedSource | null)?.setRate?.(r),
    wander: () => (sourceRef.current as SimulatedSource | null)?.wander?.() };
}
