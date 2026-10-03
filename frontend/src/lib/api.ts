import type {
  AssessResponse, CheckIn, Companions, LatLon, Meta, Profile, ResponderResponse, RouteCheckResponse, RouteResult,
  StateResponse, Metric,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

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
  meta: () => req<Meta>("/api/meta"),
  state: (t?: string | null, signal?: AbortSignal) => req<StateResponse>(`/api/state${q({ t })}`, { signal }),
  metrics: (p: LatLon, t?: string | null) => req<Metric[]>(`/api/metrics${q({ lat: p.lat, lon: p.lon, t })}`),
  // Only the fields needed for the decision are sent; nothing is stored server-side.
  assess: (body: { profile: Profile; checkin?: CheckIn | null; location: LatLon; t?: string | null; blocked?: string[] }, signal?: AbortSignal) =>
    req<AssessResponse>("/api/assess", { method: "POST", body: JSON.stringify(body), signal }),
  route: (p: {
    lat: number; lon: number; mode: "walk" | "drive"; t?: string | null; companions?: Companions | null;
    blocked?: string[]; needs_help?: boolean;
  }) => {
    const c = p.companions;
    const flags = c ? (["kids", "older_adults", "limited_mobility", "pets"] as const).filter((k) => c[k]) : [];
    return req<RouteResult>(`/api/route${q({
      lat: p.lat, lon: p.lon, mode: p.mode, t: p.t,
      companions: c ? (flags.length ? flags.join(",") : "none") : undefined,
      count: c ? c.count : undefined,
      blocked: p.blocked?.length ? p.blocked.join(",") : undefined,
      needs_help: p.needs_help || undefined,
    })}`);
  },
  checkRoute: (body: { mode: "walk" | "drive"; edge_ids: string[]; arrive_at: string; t?: string | null }) =>
    req<RouteCheckResponse>("/api/route/check", { method: "POST", body: JSON.stringify(body) }),
  responder: (t?: string | null) => req<ResponderResponse>(`/api/responder${q({ t })}`),
};
