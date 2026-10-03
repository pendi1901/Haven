import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import LocationPicker from "../components/LocationPicker";
import Map from "../components/Map";
import { storage } from "../lib/storage";
import { useStore } from "../lib/store";
import type { HomeType, Profile } from "../lib/types";

const STEPS = ["Home", "Building", "Household", "Resources"] as const;

function Choice<T>({ value, current, onChange, children }: { value: T; current: T; onChange: (v: T) => void; children: ReactNode }) {
  const on = value === current;
  return (
    <button onClick={() => onChange(on ? (null as T) : value)} aria-pressed={on}
      className={`min-h-[52px] rounded-xl border px-3 text-[15px] font-semibold transition ${on ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-white text-ink hover:border-slate-400"}`}>
      {children}
    </button>
  );
}

/** Under 60 seconds; every question skippable; saved on this device only (spec §12). */
export default function Onboarding() {
  const { meta, profile, setProfile, setLocation } = useStore();
  const [step, setStep] = useState(0);
  const [p, setP] = useState<Profile>(profile);
  const [picking, setPicking] = useState(false);
  const [homeLabel, setHomeLabel] = useState<string | null>(profile.home ? "Saved home" : null);
  const nav = useNavigate();
  const upd = (patch: Partial<Profile>) => setP((x) => ({ ...x, ...patch }));
  const toggleHH = (k: string) => upd({ household: p.household.includes(k) ? p.household.filter((x) => x !== k) : [...p.household, k] });

  const finish = () => {
    setProfile(p);
    if (p.home) setLocation({ ...p.home, label: "Home", source: "home" });
    storage.setOnboarded();
    nav("/");
  };
  const next = () => (step < STEPS.length - 1 ? setStep(step + 1) : finish());

  return (
    <div className="mx-auto flex min-h-[100dvh] max-w-xl flex-col px-5 pb-6 pt-8">
      <div className="flex items-center gap-2">
        <img src="/icon.svg" alt="" className="h-8 w-8" />
        <span className="text-xl font-bold tracking-tight">Haven</span>
        <button onClick={finish} className="ml-auto text-sm font-medium text-ink-3 hover:text-ink">Skip setup</button>
      </div>
      <div className="mt-6 flex gap-1.5" aria-label={`Step ${step + 1} of ${STEPS.length}`}>
        {STEPS.map((s, i) => <div key={s} className={`h-1.5 flex-1 rounded-full ${i <= step ? "bg-slate-900" : "bg-slate-200"}`} />)}
      </div>

      <main className="mt-6 flex-1">
        {step === 0 && (
          <>
            <h1 className="text-2xl font-bold leading-tight">Where's home?</h1>
            <p className="mt-2 text-ink-2">Haven checks official alerts, river forecasts and air and heat conditions for this spot. Your answers stay on this device.</p>
            <div className="mt-5">
              {p.home && <p className="mb-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-900">✓ Home set: {homeLabel}</p>}
              <LocationPicker onPick={(l) => { upd({ home: { lat: l.lat, lon: l.lon } }); setHomeLabel(l.label); }} onMapPick={() => setPicking(true)} />
              {picking && meta && (
                <div className="mt-3 h-72 overflow-hidden rounded-xl ring-1 ring-black/10">
                  <Map meta={meta} state={null} picking location={p.home} onPick={(l) => { upd({ home: l }); setHomeLabel("Pinned on the map"); setPicking(false); }} />
                </div>
              )}
              {p.home && (
                <details className="mt-5 rounded-xl border border-line p-3">
                  <summary className="cursor-pointer text-sm font-semibold text-ink-2">
                    {p.work ? "✓ Work location set" : "Add a work location (optional)"}
                  </summary>
                  <div className="mt-3">
                    <LocationPicker compact onPick={(l) => upd({ work: { lat: l.lat, lon: l.lon } })} />
                  </div>
                </details>
              )}
            </div>
          </>
        )}
        {step === 1 && (
          <>
            <h1 className="text-2xl font-bold leading-tight">What kind of home?</h1>
            <p className="mt-2 text-ink-2">This changes tornado and flood advice. Mobile homes need different steps.</p>
            <div className="mt-5 grid grid-cols-2 gap-2">
              {([["house", "House"], ["apartment", "Apartment"], ["mobile_home", "Mobile home"], ["basement_unit", "Basement unit"]] as [HomeType, string][]).map(([v, l]) => (
                <Choice key={v} value={v} current={p.home_type} onChange={(x) => upd({ home_type: x })}>{l}</Choice>
              ))}
            </div>
            <h2 className="mt-6 font-semibold">Which floor do you live on?</h2>
            <div className="mt-2 grid grid-cols-4 gap-2">
              {[["Ground", 0], ["1st", 1], ["2nd", 2], ["3rd+", 3]].map(([l, v]) => (
                <Choice key={v} value={v as number} current={p.floor} onChange={(x) => upd({ floor: x })}>{l}</Choice>
              ))}
            </div>
          </>
        )}
        {step === 2 && (
          <>
            <h1 className="text-2xl font-bold leading-tight">Who lives with you?</h1>
            <p className="mt-2 text-ink-2">Used for walking pace, destinations (pet-friendly, accessible) and steps. Pick any.</p>
            <div className="mt-5 grid grid-cols-2 gap-2">
              {[["kids", "Kids"], ["older_adults", "Older adults"], ["pets", "Pets"], ["limited_mobility", "Someone with limited mobility"]].map(([k, l]) => (
                <Choice key={k} value={true} current={p.household.includes(k) ? true : null} onChange={() => toggleHH(k)}>{l}</Choice>
              ))}
            </div>
          </>
        )}
        {step === 3 && (
          <>
            <h1 className="text-2xl font-bold leading-tight">A few quick details</h1>
            <p className="mt-2 text-ink-2">Each one makes advice more specific. Health answers never leave this device except to decide your verdict.</p>
            <div className="mt-5 space-y-4">
              {([
                ["usually_has_car", "Do you usually have a car?"],
                ["has_ac", "Do you have air conditioning?"],
                ["has_purifier", "Do you have an air purifier?"],
              ] as const).map(([k, q]) => (
                <div key={k}>
                  <h2 className="font-semibold">{q}</h2>
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    <Choice value={true} current={p[k]} onChange={(v) => upd({ [k]: v })}>Yes</Choice>
                    <Choice value={false} current={p[k]} onChange={(v) => upd({ [k]: v })}>No</Choice>
                  </div>
                </div>
              ))}
              <div>
                <h2 className="font-semibold">Does anyone have heart or lung conditions, or are you pregnant?</h2>
                <p className="text-sm text-ink-3">Optional. Makes smoke and heat thresholds stricter.</p>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <Choice value={true} current={p.sensitive_health ? true : null} onChange={(v) => upd({ sensitive_health: !!v })}>Yes</Choice>
                  <Choice value={false} current={p.sensitive_health ? null : false} onChange={() => upd({ sensitive_health: false })}>No</Choice>
                </div>
              </div>
            </div>
          </>
        )}
      </main>

      <div className="mt-8 flex gap-2">
        {step > 0 && <button onClick={() => setStep(step - 1)} className="min-h-[52px] rounded-xl px-5 font-semibold text-ink-2 hover:bg-slate-100">Back</button>}
        <button onClick={next} className="min-h-[52px] flex-1 rounded-xl bg-slate-900 text-base font-semibold text-white">
          {step === STEPS.length - 1 ? "Done" : "Continue"}
        </button>
      </div>
      <button onClick={next} className="mt-2 py-2 text-sm text-ink-3 hover:text-ink">Skip this question</button>
    </div>
  );
}
