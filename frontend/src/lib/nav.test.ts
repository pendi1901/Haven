import { describe, expect, it } from "vitest";
import { cumulative, project, splitAt, type XY } from "./geo";
import { OffRouteDetector, RouteTracker } from "./nav";
import type { Route } from "./types";

describe("OffRouteDetector (scenario 12)", () => {
  it("three consecutive fixes > 40 m trigger exactly one reroute", () => {
    const d = new OffRouteDetector();
    const fired = [55, 60, 70, 80, 90].map((m) => d.update(m));
    expect(fired.filter(Boolean)).toHaveLength(1);
    expect(fired[2]).toBe(true);
  });

  it("a single jittery fix does not trigger", () => {
    const d = new OffRouteDetector();
    const fired = [5, 80, 6, 4, 90, 3, 2].map((m) => d.update(m));
    expect(fired.some(Boolean)).toBe(false);
  });

  it("two far fixes then back on route resets the streak", () => {
    const d = new OffRouteDetector();
    expect([50, 50, 10, 50, 50].map((m) => d.update(m)).some(Boolean)).toBe(false);
  });

  it("a new excursion after returning to the route fires again", () => {
    const d = new OffRouteDetector();
    [50, 50, 50].forEach((m) => d.update(m));
    d.update(5);
    expect([50, 50, 50].map((m) => d.update(m)).filter(Boolean)).toHaveLength(1);
  });
});

describe("geometry", () => {
  const line: XY[] = [[-82.56, 35.58], [-82.55, 35.58], [-82.55, 35.59]];
  it("projects onto the polyline", () => {
    const pr = project([-82.555, 35.5805], line);
    expect(pr.distance).toBeGreaterThan(50);
    expect(pr.distance).toBeLessThan(60);
    expect(pr.segment).toBe(0);
  });
  it("splits at a distance", () => {
    const cum = cumulative(line);
    const [a, b] = splitAt(line, cum[1] + 100, cum);
    expect(a.length).toBe(3);
    expect(b[b.length - 1]).toEqual(line[2]);
  });
});

describe("RouteTracker", () => {
  const route = {
    label: "Least risky route in this data", mode: "walk",
    destination: { id: "d", name: "Library", kind: "refuge", label: "x", lat: 35.59, lon: -82.55, address: null,
      elevation_m: null, margin_m: null, pet_friendly: null, accessible: null, source: "osm" },
    geometry: { type: "LineString", coordinates: [[-82.56, 35.58], [-82.55, 35.58], [-82.55, 35.59]] },
    edge_ids: [], edge_starts_m: [], distance_m: 2000, duration_s: 1500, depart_at: "", arrive_at: "", deadline: null, slack_min: null,
    steps: [
      { instruction: "Head east on Main St", street: "Main St", distance_m: 900, bearing_change: null, lat: 35.58, lon: -82.56 },
      { instruction: "Turn left onto Oak St", street: "Oak St", distance_m: 1100, bearing_change: -90, lat: 35.58, lon: -82.55 },
      { instruction: "Arrive at Library", street: null, distance_m: 0, bearing_change: null, lat: 35.59, lon: -82.55 },
    ],
    speed_mps: 1.3, google_maps_url: null, apple_maps_url: null, waypoints: [], notes: [],
  } as Route;
  it("gives the next instruction with distance", () => {
    const tr = new RouteTracker(route);
    const p = tr.progress([-82.5555, 35.58]);
    expect(p.next?.step.instruction).toBe("Turn left onto Oak St");
    expect(p.next?.text).toMatch(/Turn left onto Oak St in \d+ m/);
    expect(p.arrived).toBe(false);
  });
  it("detects arrival within 30 m", () => {
    const tr = new RouteTracker(route);
    expect(tr.progress([-82.55, 35.5899]).arrived).toBe(true);
  });
});
