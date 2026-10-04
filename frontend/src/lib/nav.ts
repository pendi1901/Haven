// Live navigation logic (spec §7.8). Pure and framework-free so it is shared by
// real GPS and the replay simulator, and unit-testable.
import { cumulative, formatDistance, haversine, pointAlong, project, type XY } from "./geo";
import type { Route, RouteStep } from "./types";

export const OFF_ROUTE_M = 40;
export const OFF_ROUTE_FIXES = 3;
export const ARRIVE_M = 30;
export const HAZARD_ALERT_M = 200;

/** Off-route detection: >40 m from the route for 3 consecutive fixes (ignores
 * GPS jitter). Fires once per excursion; reset() after a reroute. */
export class OffRouteDetector {
  private streak = 0;
  private fired = false;
  constructor(private threshold = OFF_ROUTE_M, private needed = OFF_ROUTE_FIXES) {}

  /** Returns true exactly when a reroute should be requested. */
  update(distanceFromRoute: number): boolean {
    if (distanceFromRoute > this.threshold) {
      this.streak += 1;
      if (this.streak >= this.needed && !this.fired) {
        this.fired = true;
        return true;
      }
    } else {
      this.streak = 0;
      this.fired = false;
    }
    return false;
  }

  reset() {
    this.streak = 0;
    this.fired = false;
  }
}

export interface Progress {
  snapped: XY;
  along: number;
  offBy: number;
  remaining: number;
  next: { step: RouteStep; inMeters: number; text: string } | null;
  arrived: boolean;
}

export class RouteTracker {
  readonly line: XY[];
  readonly cum: number[];
  readonly total: number;
  private stepAt: number[];

  constructor(public route: Route) {
    this.line = route.geometry.coordinates as XY[];
    this.cum = cumulative(this.line);
    this.total = this.cum[this.cum.length - 1] || 0;
    // Distance along the route at which each step starts.
    let lastAlong = 0;
    this.stepAt = route.steps.map((s) => {
      const pr = project([s.lon, s.lat], this.line, this.cum);
      lastAlong = Math.max(lastAlong, pr.along);
      return lastAlong;
    });
  }

  progress(p: XY): Progress {
    const pr = project(p, this.line, this.cum);
    const dest: XY = [this.route.destination.lon, this.route.destination.lat];
    const arrived = haversine(p, dest) <= ARRIVE_M || this.total - pr.along <= ARRIVE_M / 2;
    let next: Progress["next"] = null;
    for (let i = 0; i < this.route.steps.length; i++) {
      if (this.stepAt[i] > pr.along + 5) {
        const step = this.route.steps[i];
        const inMeters = this.stepAt[i] - pr.along;
        next = { step, inMeters, text: `${step.instruction} in ${formatDistance(inMeters)}` };
        break;
      }
    }
    return { snapped: pr.point, along: pr.along, offBy: pr.distance, remaining: Math.max(0, this.total - pr.along), next, arrived };
  }
}

export type PositionFix = { lon: number; lat: number; accuracy: number; heading: number | null; t: number };
export type PositionHandler = (fix: PositionFix) => void;

export interface PositionSource {
  start(onFix: PositionHandler, onError?: (e: string) => void): void;
  stop(): void;
}

/** Real GPS via the Geolocation API (requires HTTPS; localhost is fine). */
export class GeolocationSource implements PositionSource {
  private id: number | null = null;
  start(onFix: PositionHandler, onError?: (e: string) => void) {
    if (!("geolocation" in navigator)) {
      onError?.("This browser cannot share location.");
      return;
    }
    this.id = navigator.geolocation.watchPosition(
      (p) => onFix({ lon: p.coords.longitude, lat: p.coords.latitude, accuracy: p.coords.accuracy, heading: p.coords.heading, t: p.timestamp }),
      (e) => onError?.(e.message || "Location unavailable"),
      { enableHighAccuracy: true, maximumAge: 2000, timeout: 20000 },
    );
  }
  stop() {
    if (this.id !== null) navigator.geolocation.clearWatch(this.id);
    this.id = null;
  }
}

/** Replay demo: moves a fake fix along a polyline at `speed` m/s of simulated
 * time, `rate` simulated seconds per real second. Feeds the same handler. */
export class SimulatedSource implements PositionSource {
  private timer: number | null = null;
  private along = 0;
  private cum: number[];
  constructor(private line: XY[], private speed: number, private rate: number, private onTick?: (simSeconds: number) => void,
              private detour: { atMeters: number; offsetM: number } | null = null) {
    this.cum = cumulative(line);
  }
  setLine(line: XY[]) {
    this.line = line;
    this.cum = cumulative(line);
    this.along = 0;
  }
  setRate(rate: number) {
    this.rate = rate;
  }
  /** Demo: drift sideways from here on to exercise off-route detection. */
  wander(offsetM = 90) {
    this.detour = { atMeters: this.along, offsetM };
  }
  start(onFix: PositionHandler) {
    const dtReal = 0.5;
    this.timer = window.setInterval(() => {
      const simDt = dtReal * this.rate;
      this.along += this.speed * simDt;
      const total = this.cum[this.cum.length - 1];
      let p = pointAlong(this.line, Math.min(this.along, total), this.cum);
      const ahead = pointAlong(this.line, Math.min(this.along + 5, total), this.cum);
      const heading = Math.atan2(ahead[0] - p[0], ahead[1] - p[1]) * (180 / Math.PI);
      // >= so a wander while paused takes effect on the next tick.
      if (this.detour && this.along >= this.detour.atMeters && this.along < this.detour.atMeters + 400) {
        // Optional demo of off-route detection: drift ~offset meters to the right of
        // the direction of travel. A fixed east shift does nothing on an east-west road.
        const behind = pointAlong(this.line, Math.max(this.along - 5, 0), this.cum);
        const kx = 111320 * Math.cos((p[1] * Math.PI) / 180), ky = 110540;
        const dx = (ahead[0] - behind[0]) * kx, dy = (ahead[1] - behind[1]) * ky;
        const len = Math.hypot(dx, dy);
        const [nx, ny] = len > 0 ? [dy / len, -dx / len] : [1, 0];
        p = [p[0] + (nx * this.detour.offsetM) / kx, p[1] + (ny * this.detour.offsetM) / ky];
      }
      onFix({ lon: p[0], lat: p[1], accuracy: 5, heading: (heading + 360) % 360, t: Date.now() });
      this.onTick?.(simDt);
      if (this.along >= total) this.stop();
    }, dtReal * 1000);
  }
  stop() {
    if (this.timer !== null) window.clearInterval(this.timer);
    this.timer = null;
  }
}
