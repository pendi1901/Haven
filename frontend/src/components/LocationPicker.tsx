import { useState } from "react";
import { useStore, type UserLocation } from "../lib/store";

/** Choose where Haven evaluates risk: GPS, a demo place (replay), or a map tap. */
export default function LocationPicker({ onPick, onMapPick, compact = false }: {
  onPick: (l: UserLocation) => void;
  onMapPick?: () => void;
  compact?: boolean;
}) {
  const { meta, replay, profile } = useStore();
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const gps = () => {
    if (!("geolocation" in navigator)) return setErr("This browser can't share location.");
    setBusy(true);
    navigator.geolocation.getCurrentPosition(
      (p) => {
        setBusy(false);
        const { latitude: lat, longitude: lon } = p.coords;
        const [w, s, e, n] = meta!.region.bbox;
        const m = 0.2; // the server accepts a small margin around the region
        if (lat < s - m || lat > n + m || lon < w - m || lon > e + m) {
          setErr(`You're outside the area this Haven server covers (${meta!.region.name}). ` +
            "Run a server for your region (REGION=… in .env) or pick a place below.");
          return;
        }
        onPick({ lat, lon, label: "Current location", source: "gps" });
      },
      (e) => {
        setBusy(false);
        setErr(e.code === 1 ? "Location permission was declined. Pick a place instead." : "Couldn't get your location.");
      },
      { enableHighAccuracy: true, timeout: 15000 },
    );
  };

  if (!meta) return <div className="h-24 animate-pulse rounded-xl bg-slate-100" />;
  return (
    <div className="space-y-2">
      {!replay && (
        <button onClick={gps} disabled={busy} className="flex min-h-[48px] w-full items-center justify-center gap-2 rounded-xl bg-blue-700 px-4 font-semibold text-white disabled:opacity-60">
          {busy ? "Locating…" : "Use my current location"}
        </button>
      )}
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
      {err && <p className="text-sm text-red-700">{err}</p>}
    </div>
  );
}
