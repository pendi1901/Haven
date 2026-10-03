import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "./api";
import { setTimeZone } from "./format";
import { emptyProfile, storage } from "./storage";
import type { AssessResponse, CheckIn, LatLon, Meta, Profile, StateResponse } from "./types";

export type UserLocation = LatLon & { label: string; source: "home" | "gps" | "map" | "demo" };

interface Store {
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
  profile: Profile;
  setProfile: (p: Profile) => void;
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

const REPLAY_DEFAULT = "2024-09-26T20:00:00Z"; // Thursday afternoon: watches out, river rising

export function StoreProvider({ children }: { children: ReactNode }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [metaError, setMetaError] = useState<string | null>(null);
  const [t, setTRaw] = useState<string | null>(null);
  const [scrubbing, setScrubbing] = useState(false);
  const [state, setState] = useState<StateResponse | null>(null);
  const [stateError, setStateError] = useState<string | null>(null);
  const [location, setLocationRaw] = useState<UserLocation | null>(() => {
    const l = storage.location();
    if (l) return { lat: l.lat, lon: l.lon, label: l.label ?? "Saved location", source: "map" };
    const home = storage.profile().home;
    return home ? { ...home, label: "Home", source: "home" } : null;
  });
  const [profile, setProfileRaw] = useState<Profile>(() => storage.profile());
  const [assess, setAssess] = useState<AssessResponse | null>(null);
  const [assessing, setAssessing] = useState(false);
  const [assessError, setAssessError] = useState<string | null>(null);
  const [hazardKey, setHazardKey] = useState<string | null>(null);
  const [checkin, setCheckin] = useState<CheckIn | null>(null);
  const [blocked, setBlocked] = useState<string[]>([]);
  const [version, setVersion] = useState(0);
  const replay = meta?.data_mode === "replay";

  useEffect(() => {
    api.meta().then((m) => {
      setMeta(m);
      setTimeZone(m.region.timezone);
      // A location saved under another region (e.g. Asheville while this server
      // covers Raleigh) is meaningless here: drop it so the user picks again.
      const [w, s, e, n] = m.region.bbox;
      setLocationRaw((l) => {
        if (l && (l.lat < s - 0.2 || l.lat > n + 0.2 || l.lon < w - 0.2 || l.lon > e + 0.2)) {
          storage.saveLocation(null);
          return null;
        }
        return l;
      });
      if (m.data_mode === "replay") {
        const fromUrl = new URLSearchParams(window.location.search).get("t");
        setTRaw(fromUrl ?? REPLAY_DEFAULT);
      }
    }).catch((e) => setMetaError(String(e.message ?? e)));
  }, []);

  // World state: refetch on replay time change (latest request wins) or live updates.
  const stateCtl = useRef<AbortController | null>(null);
  const loadState = useCallback((tt: string | null) => {
    stateCtl.current?.abort();
    const ctl = new AbortController();
    stateCtl.current = ctl;
    api.state(tt, ctl.signal).then((s) => {
      setState(s);
      setStateError(null);
    }).catch((e) => {
      if (e.name !== "AbortError") setStateError(String(e.message ?? e));
    });
  }, []);

  useEffect(() => {
    if (!meta) return;
    if (replay && !t) return;
    loadState(replay ? t : null);
  }, [meta, replay, t, version, loadState]);

  // Live: SSE pushes state_updated when a poller sees new data (spec §10).
  useEffect(() => {
    if (!meta || replay) return;
    const es = new EventSource("/api/stream");
    es.addEventListener("state_updated", () => setVersion((v) => v + 1));
    const fallback = window.setInterval(() => setVersion((v) => v + 1), 5 * 60 * 1000);
    return () => {
      es.close();
      window.clearInterval(fallback);
    };
  }, [meta, replay]);

  const setLocation = useCallback((l: UserLocation | null) => {
    setLocationRaw(l);
    storage.saveLocation(l ? { lat: l.lat, lon: l.lon, label: l.label } : null);
  }, []);
  const setProfile = useCallback((p: Profile) => {
    setProfileRaw(p);
    storage.saveProfile(p);
  }, []);

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
    if (!meta || !location || scrubbing || (replay && !t)) return;
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
  }, [meta, location, profile, checkin, t, scrubbing, replay, blocked, version, assessNonce]); // eslint-disable-line react-hooks/exhaustive-deps

  const setT = useCallback((tt: string, opts?: { scrubbing?: boolean }) => {
    setTRaw(tt);
    setScrubbing(!!opts?.scrubbing);
    const url = new URL(window.location.href);
    url.searchParams.set("t", tt);
    window.history.replaceState(null, "", url);
  }, []);

  const value = useMemo<Store>(() => ({
    meta, metaError, replay, t, setT, scrubbing, state, stateError, location, setLocation, profile, setProfile,
    checkin, answer, resetCheckin, assess, assessing, assessError, refreshAssess, blocked, setBlocked, version,
  }), [meta, metaError, replay, t, setT, scrubbing, state, stateError, location, setLocation, profile, setProfile,
    checkin, answer, resetCheckin, assess, assessing, assessError, refreshAssess, blocked, version]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
