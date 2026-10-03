// Small, dependency-free geometry helpers (meters on a local equirectangular
// projection; accurate to well under 1% at city scale).
export type XY = [number, number]; // [lon, lat]

const R = 6371008.8;
const rad = (d: number) => (d * Math.PI) / 180;

export function haversine(a: XY, b: XY): number {
  const dLat = rad(b[1] - a[1]);
  const dLon = rad(b[0] - a[0]);
  const s = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a[1])) * Math.cos(rad(b[1])) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

export function bearing(a: XY, b: XY): number {
  const y = Math.sin(rad(b[0] - a[0])) * Math.cos(rad(b[1]));
  const x = Math.cos(rad(a[1])) * Math.sin(rad(b[1])) - Math.sin(rad(a[1])) * Math.cos(rad(b[1])) * Math.cos(rad(b[0] - a[0]));
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

/** Cumulative distance (m) at each vertex of a polyline. */
export function cumulative(line: XY[]): number[] {
  const out = [0];
  for (let i = 1; i < line.length; i++) out.push(out[i - 1] + haversine(line[i - 1], line[i]));
  return out;
}

export interface Projection {
  point: XY;
  distance: number; // perpendicular distance from p to the line (m)
  along: number; // distance along the line to the projected point (m)
  segment: number;
}

/** Project p onto a polyline (snap to route). */
export function project(p: XY, line: XY[], cum: number[] = cumulative(line)): Projection {
  let best: Projection = { point: line[0], distance: Infinity, along: 0, segment: 0 };
  const kx = Math.cos(rad(p[1])) * R * (Math.PI / 180);
  const ky = R * (Math.PI / 180);
  for (let i = 0; i < line.length - 1; i++) {
    const a = line[i];
    const b = line[i + 1];
    const ax = (a[0] - p[0]) * kx, ay = (a[1] - p[1]) * ky;
    const bx = (b[0] - p[0]) * kx, by = (b[1] - p[1]) * ky;
    const dx = bx - ax, dy = by - ay;
    const len2 = dx * dx + dy * dy;
    const t = len2 > 0 ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len2)) : 0;
    const qx = ax + t * dx, qy = ay + t * dy;
    const d = Math.hypot(qx, qy);
    if (d < best.distance) {
      const pt: XY = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
      best = { point: pt, distance: d, along: cum[i] + (cum[i + 1] - cum[i]) * t, segment: i };
    }
  }
  return best;
}

/** Point at `dist` meters along a polyline. */
export function pointAlong(line: XY[], dist: number, cum: number[] = cumulative(line)): XY {
  if (dist <= 0) return line[0];
  const total = cum[cum.length - 1];
  if (dist >= total) return line[line.length - 1];
  let i = 0;
  while (i < cum.length - 2 && cum[i + 1] < dist) i++;
  const f = (dist - cum[i]) / (cum[i + 1] - cum[i] || 1);
  return [line[i][0] + (line[i + 1][0] - line[i][0]) * f, line[i][1] + (line[i + 1][1] - line[i][1]) * f];
}

/** Split a polyline at a distance: [traveled, remaining]. */
export function splitAt(line: XY[], dist: number, cum: number[] = cumulative(line)): [XY[], XY[]] {
  const p = pointAlong(line, dist, cum);
  let i = 0;
  while (i < cum.length - 1 && cum[i + 1] <= dist) i++;
  return [[...line.slice(0, i + 1), p], [p, ...line.slice(i + 1)]];
}

export function distanceToLine(p: XY, line: XY[]): number {
  if (line.length === 1) return haversine(p, line[0]);
  return project(p, line).distance;
}

export function formatDistance(m: number): string {
  if (m < 950) return `${Math.max(10, Math.round(m / 10) * 10)} m`;
  return `${(m / 1000).toFixed(m < 9500 ? 1 : 0)} km`;
}
