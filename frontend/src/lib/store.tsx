import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useAccount, type Account } from "./account";
import { api, setApiContext, type DataMode } from "./api";
import { setTimeZone } from "./format";
import { emptyProfile, storage } from "./storage";
import type { AssessResponse, CheckIn, LatLon, Meta, Profile, StateResponse } from "./types";

export type UserLocation = LatLon & { label: string; source: "home" | "gps" | "map" | "demo" };

interface Store {
  dataMode: DataMode;
  /** Replay scenario (region key); null = the server's default replay. */
  scenario: string | null;
  setDataMode: (m: DataMode, scenario?: string | null) => void;
  meta: Meta | null;
  metaError: string | null;
  replay: boolean;
  t: string | null;
  setT: (t: string, opts?: { scrubbing?: boolean }) => void;
  scrubbing: boolean;
  state: StateResponse | null;
  stateError: string | null;
  location: UserLocation | null;
  setLocation: (l: UserLocation | null) => void;
  locating: boolean;
  locError: string | null;
  locateMe: () => Promise<UserLocation | null>;
  profile: Profile;
  setProfile: (p: Profile) => void;
  account: Account;
  checkin: CheckIn | null;
  answer: (patch: Partial<CheckIn>) => void;
  resetCheckin: () => void;
  assess: AssessResponse | null;
  assessing: boolean;
  assessError: string | null;
  refreshAssess: () => void;
  blocked: string[];
  setBlocked: (b: string[]) => void;
  version: number;
}

const Ctx = createContext<Store | null>(null);

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore outside provider");
  return s;
}

function initialMode(): DataMode {
  const fromUrl = new URLSearchParams(window.location.search).get("mode");
  if (fromUrl === "live" || fromUrl === "replay") return fromUrl;
  return storage.mode() ?? "live";
}

function locationErrorText(e: GeolocationPositionError): string {
  const mac = /Mac/.test(navigator.platform) || /Mac OS X/.test(navigator.userAgent);
  if (e.code === e.PERMISSION_DENIED) {
    return "Location permission is blocked for this site. Allow it in the address bar's site settings, or search for your address below.";
  }
  if (e.code === e.POSITION_UNAVAILABLE) {
    return (mac
      ? "Your browser was allowed, but macOS didn't provide a position. Check System Settings → Privacy & Security → Location Services: it must be on, and on for this browser. "
      : "Your device couldn't determine a position (location services may be off). ")
      + "Meanwhile, search for your address below.";
  }
  return "Getting a location fix timed out. Try again, or search for your address below.";
}

function initialScenario(): string | null {
  return new URLSearchParams(window.location.search).get("scenario") ?? storage.scenario();
}

/** Saved locations are per mode, and per scenario in replay (a spot in Asheville means
 * nothing in the Raleigh simulation). The default replay keeps the original key. */
function locationKey(mode: DataMode, scenario: string | null): string {
  return mode === "replay" && scenario ? `replay-${scenario}` : mode;
}

function savedLocation(mode: DataMode, scenario: string | null = null): UserLocation | null {
  const l = storage.location(locationKey(mode, scenario));
  if (l) return { lat: l.lat, lon: l.lon, label: l.label ?? "Saved location", source: (l.source as UserLocation["source"]) ?? "map" };
  const home = mode === "live" ? storage.profile().home : null;
  return home ? { ...home, label: "Home", source: "home" } : null;
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [dataMode, setDataModeRaw] = useState<DataMode>(initialMode);
  const [scenario, setScenario] = useState<string | null>(initialScenario);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [metaError, setMetaError] = useState<string | null>(null);
  const [t, setTRaw] = useState<string | null>(null);
  const [scrubbing, setScrubbing] = useState(false);
  const [state, setState] = useState<StateResponse | null>(null);
  const [stateError, setStateError] = useState<string | null>(null);
  const [location, setLocationRaw] = useState<UserLocation | null>(() => savedLocation(initialMode(), initialScenario()));
  const [locating, setLocating] = useState(false);
  const [locError, setLocError] = useState<string | null>(null);
  const [profile, setProfileRaw] = useState<Profile>(() => storage.profile());
  const [assess, setAssess] = useState<AssessResponse | null>(null);
  const [assessing, setAssessing] = useState(false);
  const [assessError, setAssessError] = useState<string | null>(null);
  const [hazardKey, setHazardKey] = useState<string | null>(null);
  const [checkin, setCheckin] = useState<CheckIn | null>(null);
  const [blocked, setBlocked] = useState<string[]>([]);
  const [version, setVersion] = useState(0);
  const replay = dataMode === "replay";

  // Keep the API's request context (mode + where the user is) current. Rounded so
  // small GPS jitter does not refetch.
  const locKey = location ? `${location.lat.toFixed(3)},${location.lon.toFixed(3)}` : "none";
  setApiContext({ data_mode: dataMode, lat: location?.lat, lon: location?.lon, scenario: replay ? scenario : null });

  const setLocation = useCallback((l: UserLocation | null) => {
    setLocationRaw(l);
    storage.saveLocation(locationKey(dataMode, scenario), l ? { lat: l.lat, lon: l.lon, label: l.label, source: l.source } : null);
  }, [dataMode, scenario]);

  // Live mode is about where the user actually is: ask the browser for it.
  // Desktop browsers often cannot produce a high-accuracy (GPS) fix, so ask for a
  // normal fix first (Wi-Fi based, fast) and only then retry with high accuracy.
  const locateMe = useCallback((): Promise<UserLocation | null> => new Promise((resolve) => {
    if (!("geolocation" in navigator)) {
      setLocError("This browser can't share location. Search for your address or tap the map instead.");
      return resolve(null);
    }
    if (!window.isSecureContext) {
      setLocError("Browsers only share location on https:// or localhost. Search for your address instead.");
      return resolve(null);
    }
    setLocating(true);
    setLocError(null);
    const ok = (p: GeolocationPosition) => {
      setLocating(false);
      const l: UserLocation = { lat: p.coords.latitude, lon: p.coords.longitude, label: "Current location", source: "gps" };
      setLocation(l);
      resolve(l);
    };
    const fail = (e: GeolocationPositionError) => {
      setLocating(false);
      setLocError(locationErrorText(e));
      resolve(null);
    };
    navigator.geolocation.getCurrentPosition(ok, (e) => {
      if (e.code === e.PERMISSION_DENIED) return fail(e);
      navigator.geolocation.getCurrentPosition(ok, fail, { enableHighAccuracy: true, timeout: 25000, maximumAge: 0 });
    }, { enableHighAccuracy: false, timeout: 12000, maximumAge: 10 * 60 * 1000 });
  }), [setLocation]);

  const autoLocated = useRef(false);
  useEffect(() => {
    if (replay || autoLocated.current) return;
    if (!location || location.source === "gps") {
      // First live visit, or a saved GPS fix that may be stale: get a fresh one.
      autoLocated.current = true;
      void locateMe();
    }
  }, [replay, location, locateMe]);

  const setDataMode = useCallback((m: DataMode, sc: string | null = null) => {
    const nextScenario = m === "replay" ? sc : null;
    if (m === dataMode && nextScenario === (m === "replay" ? scenario : null)) return;
    storage.saveMode(m);
    storage.saveScenario(nextScenario);
    setDataModeRaw(m);
    setScenario(nextScenario);
    setMeta(null);
    setState(null);
    setAssess(null);
    setAssessError(null);
    setStateError(null);
    setLocationRaw(savedLocation(m, nextScenario));
    autoLocated.current = false;
    const url = new URL(window.location.href);
    url.searchParams.set("mode", m);
    if (nextScenario) url.searchParams.set("scenario", nextScenario);
    else url.searchParams.delete("scenario");
    url.searchParams.delete("t"); // a time from another scenario means nothing here
    window.history.replaceState(null, "", url);
    setTRaw(null); // replay: set from the scenario's default once its meta arrives
  }, [dataMode, scenario]);

  // Meta depends on mode and (live) on where the user is: a prepared region with
  // the road-flood overlay, or a live context built around the location.
  useEffect(() => {
    let live = true;
    setMetaError(null);
    api.meta().then((m) => {
      if (!live) return;
      setMeta(m);
      setTimeZone(m.region.timezone);
      if (m.data_mode === "replay") {
        const [w, s, e, n] = m.region.bbox;
        setLocationRaw((l) => {
          if (l && (l.lat < s - 0.2 || l.lat > n + 0.2 || l.lon < w - 0.2 || l.lon > e + 0.2)) {
            storage.saveLocation(locationKey("replay", scenario), null);
            return null;
          }
          return l;
        });
        setTRaw((cur) => cur ?? new URLSearchParams(window.location.search).get("t") ?? m.replay?.default_t ?? m.replay?.start ?? null);
      }
    }).catch((e) => live && setMetaError(String(e.message ?? e)));
    return () => { live = false; };
  }, [dataMode, scenario, replay ? "" : locKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // World state: refetch on replay time change or live updates. At most one request
  // is in flight; while it runs, only the newest wanted time is kept and fetched next.
  // Aborting instead does not stop the server computing the abandoned timesteps, so
  // scrubbing or playback would queue work faster than it drains.
  const modeRef = useRef(dataMode);
  modeRef.current = dataMode;
  const stateWanted = useRef<{ tt: string | null } | null>(null);
  const stateBusy = useRef(false);
  const loadState = useCallback(async (tt: string | null) => {
    stateWanted.current = { tt };
    if (stateBusy.current) return;
    stateBusy.current = true;
    while (stateWanted.current) {
      const want = stateWanted.current;
      stateWanted.current = null;
      try {
        const s = await api.state(want.tt);
        if (s.data_mode !== modeRef.current) continue;
        setState(s);
        setStateError(null);
      } catch (e) {
        if (!stateWanted.current) setStateError(String((e as Error).message ?? e));
      }
    }
    stateBusy.current = false;
  }, []);

  useEffect(() => {
    if (!meta || meta.data_mode !== dataMode) return;
    if (replay && !t) return;
    loadState(replay ? t : null);
  }, [meta, dataMode, replay, t, version, loadState]);

  // Live: SSE pushes state_updated when a poller sees new data (spec §10).
  useEffect(() => {
    if (!meta || replay) return;
    const es = new EventSource(api.streamUrl());
    es.addEventListener("state_updated", () => setVersion((v) => v + 1));
    const fallback = window.setInterval(() => setVersion((v) => v + 1), 5 * 60 * 1000);
    return () => {
      es.close();
      window.clearInterval(fallback);
    };
  }, [meta, replay]);

  // A profile pulled from the account keeps the account's timestamp, so it isn't
  // mistaken for a newer local edit and uploaded straight back.
  const applyProfile = useCallback((p: Profile, updatedAt: string) => {
    setProfileRaw(p);
    storage.saveProfile(p, updatedAt);
  }, []);
  const { account, pushProfile } = useAccount(profile, applyProfile);

  const setProfile = useCallback((p: Profile) => {
    setProfileRaw(p);
    storage.saveProfile(p);
    pushProfile(p);
  }, [pushProfile]);

  const answer = useCallback((patch: Partial<CheckIn>) => {
    setCheckin((c) => {
      const next = { ...(c ?? {}), ...patch, answered_at: new Date().toISOString() };
      storage.saveCheckin(hazardKey, next);
      return next;
    });
  }, [hazardKey]);
  const resetCheckin = useCallback(() => {
    setCheckin(null);
    storage.saveCheckin(hazardKey, null);
  }, [hazardKey]);

  // Assess: on location / profile / check-in / time (not while scrubbing) / live updates.
  const assessCtl = useRef<AbortController | null>(null);
  const [assessNonce, setAssessNonce] = useState(0);
  const refreshAssess = useCallback(() => setAssessNonce((n) => n + 1), []);
  useEffect(() => {
    if (!meta || meta.data_mode !== dataMode || !location || scrubbing || (replay && !t)) return;
    assessCtl.current?.abort();
    const ctl = new AbortController();
    assessCtl.current = ctl;
    setAssessing(true);
    const body = {
      profile: profile ?? emptyProfile(),
      checkin: checkin ? { ...checkin, location: { lat: location.lat, lon: location.lon } } : null,
      location: { lat: location.lat, lon: location.lon },
      t: replay ? t : null,
      blocked,
    };
    api.assess(body, ctl.signal).then((a) => {
      setAssess(a);
      setAssessError(null);
      const hz = a.crisis ? a.hazard : null;
      if (hz !== hazardKey) {
        // Check-in answers belong to one hazard; a new hazard starts fresh.
        setHazardKey(hz);
        const saved = storage.checkin(hz);
        setCheckin(saved);
      }
    }).catch((e) => {
      if (e.name !== "AbortError") setAssessError(String(e.message ?? e));
    }).finally(() => {
      if (assessCtl.current === ctl) setAssessing(false);
    });
  }, [meta, dataMode, location, profile, checkin, t, scrubbing, replay, blocked, version, assessNonce]); // eslint-disable-line react-hooks/exhaustive-deps

  const setT = useCallback((tt: string, opts?: { scrubbing?: boolean }) => {
    setTRaw(tt);
    setScrubbing(!!opts?.scrubbing);
    const url = new URL(window.location.href);
    url.searchParams.set("t", tt);
    window.history.replaceState(null, "", url);
  }, []);

  const value = useMemo<Store>(() => ({
    dataMode, scenario, setDataMode, meta, metaError, replay, t, setT, scrubbing, state, stateError, location, setLocation,
    locating, locError, locateMe, profile, setProfile, account,
    checkin, answer, resetCheckin, assess, assessing, assessError, refreshAssess, blocked, setBlocked, version,
  }), [dataMode, scenario, setDataMode, meta, metaError, replay, t, setT, scrubbing, state, stateError, location, setLocation,
    locating, locError, locateMe, profile, setProfile, account,
    checkin, answer, resetCheckin, assess, assessing, assessError, refreshAssess, blocked, version]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
