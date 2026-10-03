import { useEffect, useState } from "react";
import type { CheckIn, CheckInQuestion, Companions } from "../lib/types";

interface Props {
  questions: CheckInQuestion[];
  focus?: string | null;
  onAnswer: (patch: Partial<CheckIn>) => void;
  onSkip: (id: string) => void;
  skipped: Set<string>;
}

/** Live check-in: one question at a time, big tap targets, every question
 * skippable (spec §7.7 step 3). Floor and building height are asked together. */
export default function CheckInSheet({ questions, focus, onAnswer, onSkip, skipped }: Props) {
  const pending = questions.filter((q) => !skipped.has(q.id));
  const focused = focus ? questions.find((q) => q.field === focus || q.id === focus) : null;
  const q = focused ?? pending[0];
  if (!q) return null;
  const pair = q.id === "floor" || q.id === "building_stories"
    ? questions.filter((x) => x.id === "floor" || x.id === "building_stories")
    : [q];
  const remaining = pending.length;

  return (
    <section className="rounded-2xl border-2 border-slate-900 bg-white p-4 shadow-card" aria-label="Quick check-in">
      <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wide text-ink-3">
        <span>Quick check-in</span>
        <span>{remaining} question{remaining === 1 ? "" : "s"} left · all optional</span>
      </div>
      {pair.length === 2 ? <FloorPair qs={pair} onAnswer={onAnswer} /> : q.id === "companions" ? <CompanionsQ q={q} onAnswer={onAnswer} /> : (
        <>
          <h3 className="mt-2 text-lg font-semibold leading-snug text-ink">{q.prompt}</h3>
          <div className="mt-3 grid grid-cols-2 gap-2">
            {q.options.map((o) => (
              <button key={String(o.value)} onClick={() => onAnswer({ [q.field]: o.value } as Partial<CheckIn>)}
                className="min-h-[52px] rounded-xl border border-slate-300 bg-slate-50 px-3 text-base font-semibold text-ink active:scale-[.98] hover:bg-slate-100">
                {o.label}
              </button>
            ))}
          </div>
        </>
      )}
      <button onClick={() => pair.forEach((p) => onSkip(p.id))} className="mt-3 w-full py-2 text-sm font-medium text-ink-3 underline-offset-2 hover:underline">
        Skip this question
      </button>
    </section>
  );
}

function FloorPair({ qs, onAnswer }: { qs: CheckInQuestion[]; onAnswer: Props["onAnswer"] }) {
  const [floor, setFloor] = useState<number | null>(null);
  const [stories, setStories] = useState<number | null>(null);
  useEffect(() => {
    if (floor !== null && stories !== null) onAnswer({ floor, building_stories: Math.max(stories, floor + 1) });
  }, [floor, stories]); // eslint-disable-line react-hooks/exhaustive-deps
  const [fq, sq] = qs;
  return (
    <>
      <h3 className="mt-2 text-lg font-semibold leading-snug text-ink">Which floor are you on, and how many floors does the building have?</h3>
      <p className="mt-1 text-sm text-ink-3">Haven uses the ground elevation at your location, so you don't need to know any numbers.</p>
      {[{ q: fq, v: floor, set: setFloor, label: "You're on" }, { q: sq, v: stories, set: setStories, label: "Building has" }].map(({ q, v, set, label }) => (
        <div key={q.id} className="mt-3">
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-3">{label}</div>
          <div className="grid grid-cols-4 gap-2">
            {q.options.map((o) => (
              <button key={String(o.value)} onClick={() => set(o.value as number)} aria-pressed={v === o.value}
                className={`min-h-[48px] rounded-xl border text-base font-semibold ${v === o.value ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-slate-50 text-ink hover:bg-slate-100"}`}>
                {o.label}{q.id === "building_stories" ? "" : ""}
              </button>
            ))}
          </div>
        </div>
      ))}
    </>
  );
}

function CompanionsQ({ q, onAnswer }: { q: CheckInQuestion; onAnswer: Props["onAnswer"] }) {
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [count, setCount] = useState(0);
  const toggle = (v: string) => {
    const n = new Set(sel);
    if (v === "none") {
      n.clear();
      setCount(0);
      onAnswer({ companions: { count: 0, kids: false, older_adults: false, limited_mobility: false, pets: false } });
      return;
    }
    n.has(v) ? n.delete(v) : n.add(v);
    setSel(n);
    if (n.size && count === 0 && v !== "pets") setCount(1);
  };
  const submit = () => {
    const c: Companions = {
      count, kids: sel.has("kids"), older_adults: sel.has("older_adults"), limited_mobility: sel.has("limited_mobility"), pets: sel.has("pets"),
    };
    onAnswer({ companions: c });
  };
  return (
    <>
      <h3 className="mt-2 text-lg font-semibold text-ink">{q.prompt}</h3>
      <div className="mt-3 grid grid-cols-2 gap-2">
        {q.options.map((o) => {
          const v = String(o.value);
          const on = sel.has(v);
          return (
            <button key={v} onClick={() => toggle(v)} aria-pressed={on}
              className={`min-h-[52px] rounded-xl border px-3 text-left text-[15px] font-semibold ${on ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-slate-50 text-ink hover:bg-slate-100"} ${v === "limited_mobility" ? "col-span-2" : ""}`}>
              {o.label}
            </button>
          );
        })}
      </div>
      {sel.size > 0 && (
        <div className="mt-3 flex items-center justify-between rounded-xl bg-slate-50 p-2">
          <span className="pl-1 text-sm font-medium text-ink-2">People with you (not counting you)</span>
          <div className="flex items-center gap-2">
            <button className="h-10 w-10 rounded-lg border border-slate-300 bg-white text-xl" onClick={() => setCount(Math.max(0, count - 1))} aria-label="Fewer">−</button>
            <span className="w-6 text-center text-lg font-semibold tabular-nums">{count}</span>
            <button className="h-10 w-10 rounded-lg border border-slate-300 bg-white text-xl" onClick={() => setCount(count + 1)} aria-label="More">+</button>
          </div>
        </div>
      )}
      {sel.size > 0 && (
        <button onClick={submit} className="mt-3 min-h-[52px] w-full rounded-xl bg-slate-900 text-base font-semibold text-white">Done</button>
      )}
    </>
  );
}
