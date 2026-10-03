import { useEffect, useMemo, useRef, useState } from "react";
import { dateTime, shortDate } from "../lib/format";
import { useStore } from "../lib/store";

const SPEEDS = [1, 4, 12]; // replay steps (15 min) per second

/** Replay clock (spec §12 screen 4): scrubbing updates the map live; the verdict
 * and route recompute on release. */
export default function TimeSlider({ compact = false }: { compact?: boolean }) {
  const { meta, t, setT, scrubbing } = useStore();
  const rm = meta?.replay;
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [drag, setDrag] = useState<number | null>(null);
  const timer = useRef<number | null>(null);

  const { start, end, step } = useMemo(() => ({
    start: rm ? Date.parse(rm.start) : 0, end: rm ? Date.parse(rm.end) : 0, step: (rm?.step_minutes ?? 15) * 60000,
  }), [rm]);
  const cur = drag ?? (t ? Date.parse(t) : start);
  const steps = Math.round((end - start) / step);
  const idx = Math.round((cur - start) / step);

  useEffect(() => {
    if (!playing) return;
    timer.current = window.setInterval(() => {
      const next = Math.min(end, (t ? Date.parse(t) : start) + step);
      setT(new Date(next).toISOString());
      if (next >= end) setPlaying(false);
    }, 1000 / SPEEDS[speed]);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [playing, speed, t, end, start, step, setT]);

  if (!rm) return null;
  const days: number[] = [];
  for (let d = start; d <= end; d += 864e5) days.push(d);
  const toIso = (i: number) => new Date(start + i * step).toISOString();

  return (
    <div className={`rounded-2xl bg-slate-900 text-white shadow-sheet ${compact ? "px-3 py-2" : "px-3 py-2 sm:px-4 sm:py-3"}`}>
      <div className="flex items-center gap-3">
        <button onClick={() => setPlaying(!playing)} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-white text-slate-900"
          aria-label={playing ? "Pause replay" : "Play replay"}>
          {playing ? (
            <svg width="14" height="14" viewBox="0 0 14 14"><rect x="2" y="1" width="3.5" height="12" rx="1" fill="currentColor" /><rect x="8.5" y="1" width="3.5" height="12" rx="1" fill="currentColor" /></svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 14 14"><path d="M3 1.5v11l9-5.5z" fill="currentColor" /></svg>
          )}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-2">
            <span className="truncate text-[11px] font-semibold uppercase tracking-wider text-sky-300">Replay · {rm.event}</span>
            <button className="rounded bg-white/10 px-1.5 py-0.5 text-[11px] tabular-nums" onClick={() => setSpeed((speed + 1) % SPEEDS.length)}
              aria-label="Playback speed">
              {SPEEDS[speed]}×
            </button>
          </div>
          <div className="text-sm font-semibold tabular-nums">{dateTime(new Date(cur).toISOString())}{scrubbing ? "" : ""}</div>
        </div>
      </div>
      <input type="range" min={0} max={steps} value={idx} aria-label="Replay time" className="haven-range mt-2 w-full"
        onChange={(e) => {
          const i = Number(e.target.value);
          setDrag(start + i * step);
          setT(toIso(i), { scrubbing: true });
        }}
        onPointerUp={() => { if (drag !== null) { setT(new Date(drag).toISOString()); setDrag(null); } }}
        onKeyUp={() => { setT(toIso(idx)); setDrag(null); }}
        onTouchEnd={() => { if (drag !== null) { setT(new Date(drag).toISOString()); setDrag(null); } }}
      />
      {!compact && (
        <div className="relative mt-0.5 hidden h-4 text-[10px] text-white/60 sm:block">
          {days.map((d) => (
            <span key={d} className="absolute -translate-x-1/2" style={{ left: `${((d - start) / (end - start)) * 100}%` }}>
              {shortDate(new Date(d).toISOString()).split(",")[0]}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
