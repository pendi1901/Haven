import { useMemo, useRef, useState } from "react";
import { CATEGORY_COLOR, clock, dayClock, shortDate } from "../lib/format";
import type { GaugeStatus } from "../lib/types";

const W = 360;
const H = 200;
const M = { l: 34, r: 74, t: 12, b: 26 };
const CATS = ["action", "minor", "moderate", "major"] as const;
const WATER = "#2563eb";

interface P { t: number; v: number; kind: "obs" | "fc" }

/** Stage hydrograph: observed (solid) + official NOAA forecast (dashed), with
 * NWS flood-category bands labeled on the right. */
export default function GaugeChart({ g, now }: { g: GaugeStatus; now: string }) {
  const [hover, setHover] = useState<P | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const tNow = Date.parse(now);

  const { obs, fc, x, y, x0, x1, yMax, ticks } = useMemo(() => {
    const obs: P[] = g.observed_series.map((p) => ({ t: Date.parse(p.t), v: p.stage_ft, kind: "obs" }));
    const fc: P[] = g.forecast.map((p) => ({ t: Date.parse(p.t), v: p.stage_ft, kind: "fc" }));
    if (fc.length && obs.length && g.online) fc.unshift({ ...obs[obs.length - 1], kind: "fc" });
    const x0 = Math.min(obs[0]?.t ?? tNow - 36e5 * 24, tNow - 36e5 * 24);
    const x1 = Math.max(fc[fc.length - 1]?.t ?? tNow, tNow + 36e5 * 6);
    const vals = [...obs, ...fc].map((p) => p.v);
    const major = g.thresholds_ft.major ?? 0;
    const yMax = Math.ceil(Math.max(major * 1.15, ...vals, 1) * 1.08);
    const x = (t: number) => M.l + ((t - x0) / (x1 - x0)) * (W - M.l - M.r);
    const y = (v: number) => H - M.b - (v / yMax) * (H - M.t - M.b);
    // Day ticks at local midnight.
    const ticks: number[] = [];
    const d = new Date(x0);
    d.setHours(24, 0, 0, 0);
    for (let t = d.getTime(); t < x1; t += 864e5) ticks.push(t);
    return { obs, fc, x, y, x0, x1, yMax, ticks };
  }, [g, tNow]);

  const path = (ps: P[]) => ps.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
  const all = [...obs, ...fc.slice(fc.length && g.online ? 1 : 0)];
  const peak = fc.length ? fc.reduce((a, b) => (b.v > a.v ? b : a)) : null;

  const onMove = (e: React.PointerEvent) => {
    const r = svg.current!.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const t = x0 + ((px - M.l) / (W - M.l - M.r)) * (x1 - x0);
    let best: P | null = null;
    for (const p of all) if (!best || Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
    setHover(best);
  };

  const bands = CATS.map((c, i) => {
    const lo = g.thresholds_ft[c];
    const hi = CATS.slice(i + 1).map((n) => g.thresholds_ft[n]).find((v) => v != null) ?? yMax;
    return lo != null && lo < yMax ? { c, lo, hi: Math.min(hi, yMax) } : null;
  }).filter(Boolean) as { c: string; lo: number; hi: number }[];

  return (
    <figure className="select-none">
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} className="w-full h-auto touch-none" onPointerMove={onMove} onPointerLeave={() => setHover(null)}
        role="img" aria-label={`Stage at ${g.short_name}: observed and official forecast`}>
        {bands.map((b) => (
          <g key={b.c}>
            <rect x={M.l} width={W - M.l - M.r} y={y(b.hi)} height={y(b.lo) - y(b.hi)} fill={CATEGORY_COLOR[b.c]} opacity={0.13} />
            <line x1={M.l} x2={W - M.r} y1={y(b.lo)} y2={y(b.lo)} stroke={CATEGORY_COLOR[b.c]} strokeWidth={1} opacity={0.7} />
            <text x={W - M.r + 6} y={y(b.lo) + 4} fontSize={10} fill="#475569">
              {b.c[0].toUpperCase() + b.c.slice(1)} {b.lo}
            </text>
          </g>
        ))}
        {[0, yMax / 2, yMax].map((v) => (
          <text key={v} x={M.l - 6} y={y(v) + 3} fontSize={10} fill="#64748b" textAnchor="end">{Math.round(v)}</text>
        ))}
        <line x1={M.l} x2={W - M.r} y1={y(0)} y2={y(0)} stroke="#cbd5e1" />
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={M.t} y2={H - M.b} stroke="#e2e8f0" />
            <text x={x(t) + 3} y={H - M.b + 14} fontSize={10} fill="#64748b">{shortDate(new Date(t).toISOString()).split(",")[0]}</text>
          </g>
        ))}
        <line x1={x(tNow)} x2={x(tNow)} y1={M.t} y2={H - M.b} stroke="#0f172a" strokeDasharray="2 2" />
        <text x={x(tNow) + 3} y={H - M.b - 4} fontSize={10} fill="#0f172a" fontWeight={600}>Now</text>
        {obs.length > 1 && <path d={path(obs)} fill="none" stroke={WATER} strokeWidth={2} strokeLinejoin="round" />}
        {fc.length > 1 && <path d={path(fc)} fill="none" stroke={WATER} strokeWidth={2} strokeDasharray="5 4" />}
        {peak && (
          <g>
            <circle cx={x(peak.t)} cy={y(peak.v)} r={4} fill="#fff" stroke={WATER} strokeWidth={2} />
            <text x={Math.min(x(peak.t), W - M.r - 30)} y={y(peak.v) - 8 < M.t + 8 ? y(peak.v) + 16 : y(peak.v) - 8} fontSize={10} fill="#0f172a"
              textAnchor="middle" fontWeight={600} stroke="#fff" strokeWidth={3} paintOrder="stroke">
              Crest {peak.v.toFixed(1)} ft
            </text>
          </g>
        )}
        {hover && (
          <g pointerEvents="none">
            <line x1={x(hover.t)} x2={x(hover.t)} y1={M.t} y2={H - M.b} stroke="#0f172a" opacity={0.35} />
            <circle cx={x(hover.t)} cy={y(hover.v)} r={4.5} fill={WATER} stroke="#fff" strokeWidth={2} />
          </g>
        )}
      </svg>
      <figcaption className="mt-1 flex flex-wrap items-center justify-between gap-2 text-xs text-ink-3">
        <span className="flex items-center gap-3">
          <span className="flex items-center gap-1"><svg width="18" height="6"><line x1="0" x2="18" y1="3" y2="3" stroke={WATER} strokeWidth="2" /></svg>Observed</span>
          <span className="flex items-center gap-1"><svg width="18" height="6"><line x1="0" x2="18" y1="3" y2="3" stroke={WATER} strokeWidth="2" strokeDasharray="5 4" /></svg>Official forecast</span>
        </span>
        <span className="tabular-nums text-ink-2">
          {hover ? `${dayClock(new Date(hover.t).toISOString())} · ${hover.v.toFixed(2)} ft ${hover.kind === "fc" ? "(forecast)" : ""}` : `Stage, ft · ${clock(now)}`}
        </span>
      </figcaption>
    </figure>
  );
}
