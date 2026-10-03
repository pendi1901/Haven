import type {
  AssessResponse, CheckIn, Companions, LatLon, Meta, Profile, ResponderResponse, RouteCheckResponse, RouteResult,
  StateResponse, Metric,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export type DataMode = "live" | "replay";

/** Which context every request is about: live or replay, and where the user is.
 * The server picks a prepared region (with the road-flood overlay) when the
 * location is inside one, and otherwise builds a live context around it. */
const ctx: { data_mode: DataMode; lat?: number; lon?: number } = { data_mode: "live" };
export function setApiContext(c: { data_mode: DataMode; lat?: number; lon?: number }) {
  ctx.data_mode = c.data_mode;
  ctx.lat = c.lat;
  ctx.lon = c.lon;
}
const where = () => ({ data_mode: ctx.data_mode, lat: ctx.lat, lon: ctx.lon });

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { "content-type": "application/json", ...(init?.headers ?? {}) } });
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const js = await r.json();
      msg = typeof js.detail === "string" ? js.detail : JSON.stringify(js.detail ?? js);
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, msg);
  }
  return r.json() as Promise<T>;
}

const q = (params: Record<string, string | number | boolean | null | undefined>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  const str = s.toString();
  return str ? `?${str}` : "";
};

export const api = {
  meta: () => req<Meta>(`/api/meta${q(where())}`),
  state: (t?: string | null, signal?: AbortSignal) => req<StateResponse>(`/api/state${q({ ...where(), t })}`, { signal }),
  metrics: (p: LatLon, t?: string | null) => req<Metric[]>(`/api/metrics${q({ ...where(), lat: p.lat, lon: p.lon, t })}`),
  // Only the fields needed for the decision are sent; nothing is stored server-side.
  assess: (body: { profile: Profile; checkin?: CheckIn | null; location: LatLon; t?: string | null; blocked?: string[] }, signal?: AbortSignal) =>
    req<AssessResponse>("/api/assess", { method: "POST", body: JSON.stringify({ ...body, data_mode: ctx.data_mode }), signal }),
  route: (p: {
    lat: number; lon: number; mode: "walk" | "drive"; t?: string | null; companions?: Companions | null;
    blocked?: string[]; needs_help?: boolean; purpose?: "flood" | "center" | "fire";
  }) => {
    const c = p.companions;
    const flags = c ? (["kids", "older_adults", "limited_mobility", "pets"] as const).filter((k) => c[k]) : [];
    return req<RouteResult>(`/api/route${q({
      data_mode: ctx.data_mode,
      lat: p.lat, lon: p.lon, mode: p.mode, t: p.t,
      companions: c ? (flags.length ? flags.join(",") : "none") : undefined,
      count: c ? c.count : undefined,
      blocked: p.blocked?.length ? p.blocked.join(",") : undefined,
      needs_help: p.needs_help || undefined,
      purpose: p.purpose,
    })}`);
  },
  checkRoute: (body: { mode: "walk" | "drive"; edge_ids: string[]; arrive_at: string; t?: string | null; lat: number; lon: number }) =>
    req<RouteCheckResponse>("/api/route/check", { method: "POST", body: JSON.stringify({ ...body, data_mode: ctx.data_mode }) }),
  responder: (t?: string | null) => req<ResponderResponse>(`/api/responder${q({ ...where(), t })}`),
  streamUrl: () => `/api/stream${q(where())}`,
  geocode: (query: string) => req<{ label: string; lat: number; lon: number }[]>(`/api/geocode${q({ q: query })}`),
};
