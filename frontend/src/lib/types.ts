// Mirrors backend/app/models.py (spec §6).

export type Source =
  | "nws" | "nwps" | "usgs" | "airnow" | "purpleair" | "epa" | "firms" | "nhc" | "ncdot" | "fema" | "osm" | "haven"
  | "simulated";
export type TimeLayer = "now" | "forecast";
export type DataMode = "live" | "replay";

export interface LatLon { lat: number; lon: number }

export interface HazardZone {
  id: string;
  hazard: string;
  geometry: GeoJSON.Geometry;
  severity: number;
  layer: TimeLayer;
  valid_from: string;
  valid_to: string | null;
  source: Source;
  reason: string;
  event?: string | null;
  headline?: string | null;
  description?: string | null;
  instruction?: string | null;
  is_warning: boolean;
  archived: boolean;
}

export interface ForecastPoint { t: string; stage_ft: number }

export type FloodCategory = "none" | "action" | "minor" | "moderate" | "major";

export interface GaugeStatus {
  lid: string;
  usgs_id: string | null;
  name: string;
  short_name: string;
  lat: number;
  lon: number;
  observed_stage_ft: number | null;
  observed_at: string | null;
  thresholds_ft: Partial<Record<Exclude<FloodCategory, "none">, number>>;
  category_now: FloodCategory;
  forecast: ForecastPoint[];
  forecast_issued_at: string | null;
  forecast_source: string | null;
  forecast_peak_ft: number | null;
  forecast_peak_at: string | null;
  forecast_peak_category: FloodCategory;
  time_to_category: Record<string, string | null>;
  online: boolean;
  stale: boolean;
  note: string | null;
  datum_navd88_ft: number | null;
  observed_series: ForecastPoint[];
  record_crest_ft: number | null;
}

export interface Metric {
  key: string;
  label: string;
  value: number | null;
  unit: string;
  category: string;
  severity: number;
  advice: string;
  source: Source;
  layer: TimeLayer;
  observed_or_valid_at: string | null;
  stale: boolean;
  available: boolean;
  detail: string | null;
}

export type HomeType = "house" | "apartment" | "mobile_home" | "basement_unit";

export interface Profile {
  home: LatLon | null;
  work: LatLon | null;
  home_type: HomeType | null;
  floor: number | null;
  household: string[];
  sensitive_health: boolean;
  usually_has_car: boolean | null;
  has_ac: boolean | null;
  has_purifier: boolean | null;
  language: string;
}

export interface Companions {
  count: number;
  kids: boolean;
  older_adults: boolean;
  limited_mobility: boolean;
  pets: boolean;
}

export interface CheckIn {
  location?: LatLon | null;
  place?: string | null;
  floor?: number | null;
  building_stories?: number | null;
  has_vehicle_now?: boolean | null;
  companions?: Companions | null;
  water_entering?: boolean | null;
  power_on?: boolean | null;
  needs_help?: boolean | null;
  answered_at?: string | null;
}

export interface RouteStep {
  instruction: string;
  street: string | null;
  distance_m: number;
  bearing_change: number | null;
  lat: number;
  lon: number;
}

export interface Destination {
  id: string;
  name: string;
  kind: "open_shelter" | "refuge" | "high_ground" | "hospital" | "upper_floor";
  label: string;
  lat: number;
  lon: number;
  address: string | null;
  elevation_m: number | null;
  margin_m: number | null;
  pet_friendly: boolean | null;
  accessible: boolean | null;
  source: Source;
}

export interface Route {
  label: string;
  mode: "walk" | "drive";
  destination: Destination;
  geometry: GeoJSON.LineString;
  edge_ids: string[];
  edge_starts_m: number[];
  distance_m: number;
  duration_s: number;
  depart_at: string;
  arrive_at: string;
  deadline: string | null;
  slack_min: number | null;
  steps: RouteStep[];
  speed_mps: number | null;
  google_maps_url: string | null;
  apple_maps_url: string | null;
  waypoints: LatLon[];
  notes: string[];
}

export interface RouteResult {
  route: Route | null;
  backup: Route | null;
  message: string | null;
  considered: number;
  t: string;
}

export interface Assumption { field: string; text: string }

export interface Verdict {
  level: number;
  label: string;
  emoji: string;
  reason: string;
  timeline: string[];
  steps: string[];
  route: Route | null;
  backup_route: Route | null;
  fallback: string | null;
  assumptions: Assumption[];
  sources: Source[];
  alerts: HazardZone[];
  rephrased: string | null;
}

export interface CheckInOption { label: string; value: unknown }
export interface CheckInQuestion {
  id: string;
  field: keyof CheckIn;
  prompt: string;
  options: CheckInOption[];
  multi: boolean;
  skippable: boolean;
}

export interface LocationRisk {
  ground_elev_m: number | null;
  in_corridor: boolean;
  reach: string | null;
  distance_to_river_m: number | null;
  water_elev_now_m: number | null;
  water_elev_forecast_max_m: number | null;
  t_flood_here: string | null;
  flooded_now: boolean;
  cut_off_now: boolean;
}

export interface AssessResponse {
  crisis: boolean;
  hazard: string | null;
  triggers: string[];
  verdict: Verdict;
  questions: CheckInQuestion[];
  risk: LocationRisk | null;
  t: string;
  data_mode: DataMode;
}

export interface RoadProps {
  id: string;
  name: string | null;
  status: "dry" | "at_risk" | "flooded" | "closed";
  first_flood_time_forecast: string | null;
  depth_ft: number | null;
  bridge: boolean;
  path: boolean;
  stale: boolean;
}

export interface SourceStatus {
  fetched_at: string | null;
  stale: boolean;
  error: string | null;
  configured: boolean;
}

export interface StateResponse {
  data_mode: DataMode;
  t: string;
  gauges: GaugeStatus[];
  zones: HazardZone[];
  roads: GeoJSON.FeatureCollection<GeoJSON.LineString, RoadProps>;
  metrics_region: Metric[];
  storms: { id: string; name: string; issued: string; advisory?: number }[];
  fires: { lat: number; lon: number; acq: string; frp: number }[];
  shelters: { id: string; name: string; lat: number; lon: number; address?: string }[];
  counts: { flooded: number; at_risk: number; closed: number; forecast_flooded: number };
  sources: Record<string, SourceStatus | Record<string, unknown>>;
  version: number;
}

export interface Meta {
  data_mode: DataMode;
  modes: { live: boolean; replay: boolean };
  /** Replay scenarios: archived events and simulated demos. The first is the default. */
  scenarios: Scenario[];
  region: {
    key: string; name: string; bbox: [number, number, number, number]; center: [number, number]; timezone: string;
    overlay: boolean; point: boolean;
  };
  overlay_regions: { key: string; name: string; bbox: [number, number, number, number] }[];
  demo_places: { name: string; lat: number; lon: number }[];
  reaches: { id: string; river: string; upstream: string; downstream: string | null; geometry: GeoJSON.LineString }[];
  replay: ReplayMeta | null;
  features: { gemini: boolean; handoff: boolean };
  dem_source: string;
}

export interface ReplayMeta {
  start: string; end: string; step_minutes: number; event: string; timezone: string;
  scenario: string; label: string | null; default_t: string; simulated: boolean;
}

export interface Scenario { key: string; label: string; event: string; simulated: boolean }

export interface ResponderResponse {
  t: string;
  available: boolean;
  hospitals: { name: string; lat: number; lon: number }[];
  cut_off_areas: GeoJSON.FeatureCollection;
  population: number;
  priority_list: {
    geoid: string; name: string; share_cut_off: number; population: number; population_cut_off_est: number;
    age65_cut_off_est?: number; no_vehicle_households_cut_off_est?: number;
  }[];
  acs: boolean;
  accuracy: null | {
    as_of: string; closures_in_corridor: number; recall: number; flagged_drive_edges: number;
    precision_lower_bound: number | null; closures: { road: string; reason: string; matched: boolean }[]; note: string;
  };
  notes: string[];
}

export interface RouteCheckResponse {
  ok: boolean;
  t: string;
  problems: { edge_id: string; name: string | null; status: string; first_flood: string | null }[];
}
