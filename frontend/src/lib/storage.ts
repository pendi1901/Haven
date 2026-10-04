// Device-only storage (spec §14). Every access is guarded: private windows and
// blocked storage must never break the app.
import type { CheckIn, LatLon, Profile, Route } from "./types";

const K = {
  profile: "haven.profile.v1",
  profileUpdatedAt: "haven.profileUpdatedAt.v1", // decides which side wins when an account syncs
  onboarded: "haven.onboarded.v1",
  checkin: "haven.checkin.v1",
  route: "haven.activeRoute.v1",
  location: "haven.location.v2", // per data mode: live and replay never share a spot
  mode: "haven.mode.v1",
  scenario: "haven.scenario.v1", // which replay: an archived event or a simulated demo
};

function read<T>(key: string): T | null {
  try {
    const v = localStorage.getItem(key);
    return v ? (JSON.parse(v) as T) : null;
  } catch {
    return null;
  }
}
function write(key: string, v: unknown) {
  try {
    if (v === null || v === undefined) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(v));
  } catch {
    /* storage unavailable */
  }
}

export const emptyProfile = (): Profile => ({
  home: null, work: null, home_type: null, floor: null, household: [], sensitive_health: false,
  usually_has_car: null, has_ac: null, has_purifier: null, language: "en",
});

export const storage = {
  profile: (): Profile => ({ ...emptyProfile(), ...(read<Profile>(K.profile) ?? {}) }),
  saveProfile: (p: Profile, updatedAt: string = new Date().toISOString()) => {
    write(K.profile, p);
    write(K.profileUpdatedAt, updatedAt);
  },
  profileUpdatedAt: () => read<string>(K.profileUpdatedAt),
  onboarded: () => read<boolean>(K.onboarded) === true,
  setOnboarded: () => write(K.onboarded, true),
  // Check-in answers expire after 3 h or when the hazard changes (spec §6).
  checkin: (hazard: string | null): CheckIn | null => {
    const v = read<{ hazard: string | null; checkin: CheckIn }>(K.checkin);
    if (!v || v.hazard !== hazard) return null;
    const at = v.checkin.answered_at ? Date.parse(v.checkin.answered_at) : 0;
    if (Date.now() - at > 3 * 3600 * 1000) return null;
    return v.checkin;
  },
  saveCheckin: (hazard: string | null, c: CheckIn | null) => write(K.checkin, c ? { hazard, checkin: c } : null),
  // Low-connectivity fallback: the active route survives reloads and dropped signal.
  activeRoute: () => read<Route>(K.route),
  saveActiveRoute: (r: Route | null) => write(K.route, r),
  location: (mode: string) => read<LatLon & { label?: string; source?: string }>(`${K.location}.${mode}`),
  saveLocation: (mode: string, l: (LatLon & { label?: string; source?: string }) | null) => write(`${K.location}.${mode}`, l),
  /** Sign-out: forget everything personal on this device (the account keeps its copy). */
  clearPersonal: () => {
    // haven.location.v1 is the pre-v2 saved location, which may still hold a home address.
    for (const k of [K.profile, K.profileUpdatedAt, K.onboarded, K.checkin, K.route, `${K.location}.live`, "haven.location.v1"]) write(k, null);
  },
  mode: () => read<"live" | "replay">(K.mode),
  saveMode: (m: "live" | "replay") => write(K.mode, m),
  scenario: () => read<string>(K.scenario),
  saveScenario: (s: string | null) => write(K.scenario, s),
};
