// Time and label formatting. All times display in the region's timezone so a
// replay of Helene reads in Asheville local time wherever the viewer is.
let TZ = "America/New_York";
export const setTimeZone = (tz: string) => (TZ = tz);
export const timeZone = () => TZ;

const fmt = (opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("en-US", { timeZone: TZ, ...opts });

export function clock(iso: string | null | undefined): string {
  if (!iso) return "—";
  return fmt({ hour: "numeric", minute: "2-digit" }).format(new Date(iso));
}
export function dayClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  return fmt({ weekday: "short", hour: "numeric", minute: "2-digit" }).format(new Date(iso));
}
export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return fmt({ month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }).format(new Date(iso));
}
export function shortDate(iso: string): string {
  return fmt({ weekday: "short", month: "short", day: "numeric" }).format(new Date(iso));
}

export function ago(iso: string | null | undefined, now: Date): string {
  if (!iso) return "no data";
  const diff = Math.round((now.getTime() - Date.parse(iso)) / 60000);
  const m = Math.abs(diff);
  const span = m < 1 ? "" : m < 60 ? `${m} min` : m < 48 * 60 ? `${Math.round(m / 60)} h` : `${Math.round(m / 1440)} d`;
  if (!span) return "just now";
  return diff >= 0 ? `${span} ago` : `in ${span}`;
}

export function duration(s: number): string {
  const m = Math.round(s / 60);
  if (m < 60) return `${Math.max(1, m)} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

export const SOURCE_LABEL: Record<string, string> = {
  nws: "NWS", nwps: "NOAA NWPS", usgs: "USGS", airnow: "AirNow", purpleair: "PurpleAir", epa: "EPA", firms: "NASA FIRMS",
  nhc: "NHC", ncdot: "NCDOT", fema: "FEMA", osm: "OpenStreetMap", haven: "Haven overlay",
  simulated: "Simulated (demo)",
};

export const CATEGORY_LABEL: Record<string, string> = {
  none: "Below flood stage", action: "Action stage", minor: "Minor flooding", moderate: "Moderate flooding", major: "Major flooding",
};

// Some gauges (lake levels, local network gauges) have no NOAA flood stages, so
// "below flood stage" would claim something NOAA never said.
export const gaugeCategoryLabel = (g: { category_now: string; thresholds_ft: Record<string, number> }) =>
  g.category_now === "none" && Object.keys(g.thresholds_ft).length === 0 ? "No NOAA flood stages" : CATEGORY_LABEL[g.category_now];

export const CATEGORY_COLOR: Record<string, string> = {
  none: "#16a34a", action: "#e6c300", minor: "#ff9900", moderate: "#e60000", major: "#b833ff",
};

export const LEVEL_COLOR: Record<number, string> = {
  0: "#15803d", 1: "#a16207", 2: "#b45309", 3: "#6d28d9", 4: "#c2410c", 5: "#b91c1c",
};
