import { useState } from "react";
import { api, ApiError } from "../lib/api";
import { useStore, type UserLocation } from "../lib/store";

/** Choose where Haven evaluates risk: GPS, a demo place (replay), or a map tap. */
export default function LocationPicker({ onPick, onMapPick, compact = false }: {
  onPick: (l: UserLocation) => void;
  onMapPick?: () => void;
  compact?: boolean;
}) {
  const { meta, replay, profile, locateMe, locating, locError } = useStore();
  const gps = async () => {
    const l = await locateMe();
    if (l) onPick(l);
  };
  const busy = locating;

  if (!meta) return <div className="h-24 animate-pulse rounded-xl bg-slate-100" />;
  return (
    <div className="space-y-2">
      {!replay && (
        <button onClick={gps} disabled={busy} className="flex min-h-[48px] w-full items-center justify-center gap-2 rounded-xl bg-blue-700 px-4 font-semibold text-white disabled:opacity-60">
          {busy ? "Finding your location… allow it if asked" : "Use my current location"}
        </button>
      )}
      {!replay && locError && !busy && (
        <p role="alert" className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">{locError}</p>
      )}
      {!replay && <PlaceSearch onPick={onPick} />}
      {profile.home && (
        <button onClick={() => onPick({ ...profile.home!, label: "Home", source: "home" })}
          className="w-full rounded-xl border border-line bg-white px-4 py-3 text-left text-sm font-semibold text-ink hover:bg-slate-50">
          Home
        </button>
      )}
      {profile.work && (
        <button onClick={() => onPick({ ...profile.work!, label: "Work", source: "home" })}
          className="w-full rounded-xl border border-line bg-white px-4 py-3 text-left text-sm font-semibold text-ink hover:bg-slate-50">
          Work
        </button>
      )}
      {replay && meta && (
        <div>
          {!compact && <p className="mb-2 text-sm text-ink-2">Replay is set in Asheville during Hurricane Helene. Pick a place to stand in:</p>}
          <div className="grid gap-2 sm:grid-cols-2">
            {meta.demo_places.map((p) => (
              <button key={p.name} onClick={() => onPick({ lat: p.lat, lon: p.lon, label: p.name, source: "demo" })}
                className="rounded-xl border border-line bg-white px-3 py-2.5 text-left text-sm font-medium text-ink hover:border-slate-400">
                {p.name}
              </button>
            ))}
          </div>
        </div>
      )}
      {onMapPick && (
        <button onClick={onMapPick} className="w-full rounded-xl border border-dashed border-slate-400 px-4 py-3 text-sm font-semibold text-ink-2 hover:bg-slate-50">
          Tap a spot on the map
        </button>
      )}
    </div>
  );
}

const UNAVAILABLE = "Place search is unavailable right now. Use your current location or tap a spot on the map instead.";

/** A short, actionable message for a failed place search (never a raw "Failed to fetch"). */
export function searchError(ex: unknown): string {
  if (ex instanceof ApiError) {
    if (ex.status >= 500 && ex.message && ex.message !== "Internal Server Error") return ex.message;
    return UNAVAILABLE;
  }
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    return "You're offline, so place search can't run. Tap a spot on the map instead.";
  }
  return "Couldn't reach the Haven server. Check your connection and try again.";
}

/** Address / place search (OpenStreetMap Nominatim via the backend). Searches on
 * submit only, per Nominatim's usage policy. */
function PlaceSearch({ onPick }: { onPick: (l: UserLocation) => void }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<{ label: string; lat: number; lon: number }[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const query = q.trim();
    if (query.length < 2) {
      setErr("Type at least 2 characters, e.g. a street address with city and state.");
      return;
    }
    setErr(null);
    setResults(null);
    setBusy(true);
    try {
      const r = await api.geocode(query);
      setResults(r);
      if (!r.length) setErr("No US place found. Try a street address with city and state.");
    } catch (ex) {
      setErr(searchError(ex));
    } finally {
      setBusy(false);
    }
  };
  const pick = (r: { label: string; lat: number; lon: number }) => {
    const label = r.label.split(",").slice(0, 2).join(",");
    onPick({ lat: r.lat, lon: r.lon, label, source: "map" });
    setQ(label);
    setResults(null);
    setErr(null);
  };
  return (
    <div>
      <form role="search" onSubmit={submit} className="flex gap-2">
        <input value={q} onChange={(e) => { setQ(e.target.value); setErr(null); }}
          placeholder="Or search an address or place, e.g. Raleigh, NC"
          aria-label="Search an address or place"
          type="search" inputMode="search" enterKeyHint="search" autoComplete="off" autoCorrect="off" spellCheck={false}
          className="min-h-[44px] min-w-0 flex-1 rounded-xl border border-slate-300 bg-white px-3 text-base outline-none focus:border-slate-900 sm:text-sm" />
        <button type="submit" disabled={busy} className="min-h-[44px] rounded-xl bg-slate-900 px-4 text-sm font-semibold text-white disabled:opacity-60">
          {busy ? "Searching…" : "Search"}
        </button>
      </form>
      <p aria-live="polite" className="mt-1 text-sm text-amber-800 empty:hidden">{err}</p>
      {results && results.length > 0 && (
        <ul aria-label="Search results" className="mt-2 overflow-hidden rounded-xl border border-line bg-white">
          {results.map((r, i) => (
            <li key={`${i}-${r.lat},${r.lon}`}>
              <button type="button" onClick={() => pick(r)}
                className="w-full border-b border-line px-3 py-2 text-left text-sm last:border-0 hover:bg-slate-50">
                {r.label}
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-1 text-[11px] text-ink-3">Search by OpenStreetMap Nominatim.</p>
    </div>
  );
}
