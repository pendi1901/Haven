"""Domain types (spec §6). Mirrored in frontend/src/lib/types.ts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Source(str, Enum):
    NWS = "nws"
    NWPS = "nwps"
    USGS = "usgs"
    AIRNOW = "airnow"
    PURPLEAIR = "purpleair"
    EPA = "epa"
    FIRMS = "firms"
    NHC = "nhc"
    NCDOT = "ncdot"
    FEMA = "fema"
    OSM = "osm"
    HAVEN = "haven"  # Haven's own deterministic overlays (never a prediction)
    SIMULATED = "simulated"  # made-up demo scenario data (python -m app.replay.simulate)


class TimeLayer(str, Enum):
    NOW = "now"
    FORECAST = "forecast"


class LatLon(BaseModel):
    lat: float
    lon: float


class HazardZone(BaseModel):
    id: str
    hazard: str  # "river_flood", "nws_alert", "wildfire", "hurricane", ...
    geometry: dict[str, Any]  # GeoJSON geometry
    severity: int  # 0-4, spec §8
    layer: TimeLayer
    valid_from: datetime
    valid_to: datetime | None
    source: Source
    reason: str
    # Alert-specific display fields, kept verbatim from the issuing office.
    event: str | None = None
    headline: str | None = None
    description: str | None = None
    instruction: str | None = None
    is_warning: bool = False
    archived: bool = False


class ForecastPoint(BaseModel):
    t: datetime
    stage_ft: float


class SeriesPoint(BaseModel):
    t: datetime
    stage_ft: float


class GaugeStatus(BaseModel):
    lid: str
    usgs_id: str | None
    name: str
    short_name: str
    lat: float
    lon: float
    observed_stage_ft: float | None
    observed_at: datetime | None
    thresholds_ft: dict[str, float]
    category_now: str
    forecast: list[ForecastPoint]
    forecast_issued_at: datetime | None = None
    forecast_source: str | None = None
    forecast_peak_ft: float | None = None
    forecast_peak_at: datetime | None = None
    forecast_peak_category: str
    time_to_category: dict[str, datetime | None]
    online: bool
    stale: bool = False
    note: str | None = None
    datum_navd88_ft: float | None = None
    observed_series: list[SeriesPoint] = Field(default_factory=list)
    record_crest_ft: float | None = None


class Metric(BaseModel):
    key: str
    label: str
    value: float | None
    unit: str
    category: str
    severity: int
    advice: str
    source: Source
    layer: TimeLayer
    observed_or_valid_at: datetime | None
    stale: bool = False
    available: bool = True
    detail: str | None = None


class RoadStatus(str, Enum):
    DRY = "dry"
    AT_RISK = "at_risk"
    FLOODED = "flooded"
    CLOSED = "closed"


HomeType = Literal["house", "apartment", "mobile_home", "basement_unit"]


class Profile(BaseModel):
    """Stored on the device; sent with requests and never persisted server-side."""

    home: LatLon | None = None
    work: LatLon | None = None
    home_type: HomeType | None = None
    floor: int | None = None
    household: list[str] = Field(default_factory=list)
    sensitive_health: bool = False
    usually_has_car: bool | None = None
    has_ac: bool | None = None
    has_purifier: bool | None = None
    language: str = "en"


class Companions(BaseModel):
    count: int = 0
    kids: bool = False
    older_adults: bool = False
    limited_mobility: bool = False
    pets: bool = False


class CheckIn(BaseModel):
    location: LatLon | None = None
    place: str | None = None  # "home","work","car","outside","other"
    floor: int | None = None  # 0 = ground
    building_stories: int | None = None
    has_vehicle_now: bool | None = None
    companions: Companions | None = None
    water_entering: bool | None = None
    power_on: bool | None = None
    needs_help: bool | None = None
    answered_at: datetime | None = None


class RouteStep(BaseModel):
    instruction: str
    street: str | None
    distance_m: float
    bearing_change: float | None = None
    lat: float
    lon: float


class Destination(BaseModel):
    id: str
    name: str
    kind: Literal["open_shelter", "refuge", "high_ground", "hospital", "upper_floor"]
    label: str
    lat: float
    lon: float
    address: str | None = None
    elevation_m: float | None = None
    margin_m: float | None = None  # height above forecast peak water level
    pet_friendly: bool | None = None
    accessible: bool | None = None
    source: Source


class Route(BaseModel):
    label: str = "Least risky route in this data"
    mode: Literal["walk", "drive"]
    destination: Destination
    geometry: dict[str, Any]  # GeoJSON LineString
    edge_ids: list[str]
    edge_starts_m: list[float] = Field(default_factory=list)  # distance along the route where each edge starts
    distance_m: float
    duration_s: float
    depart_at: datetime
    arrive_at: datetime
    deadline: datetime | None  # earliest forecast flooding on the route minus buffer
    slack_min: float | None
    steps: list[RouteStep]
    speed_mps: float | None
    google_maps_url: str | None
    apple_maps_url: str | None
    waypoints: list[LatLon] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RouteResult(BaseModel):
    route: Route | None
    backup: Route | None
    message: str | None = None
    considered: int = 0
    t: datetime


class Verdict(BaseModel):
    level: int  # 0-5, spec §9
    label: str
    emoji: str
    reason: str
    timeline: list[str]
    steps: list[str]
    route: Route | None = None
    backup_route: Route | None = None
    fallback: str | None = None
    assumptions: list[dict[str, str]] = Field(default_factory=list)  # {field, text}
    sources: list[Source]
    alerts: list[HazardZone] = Field(default_factory=list)  # official text shown first
    rephrased: str | None = None


class CheckInOption(BaseModel):
    label: str
    value: Any


class CheckInQuestion(BaseModel):
    id: str
    field: str
    prompt: str
    options: list[CheckInOption]
    multi: bool = False
    skippable: bool = True


class AssessRequest(BaseModel):
    data_mode: Literal["live", "replay"] | None = None
    profile: Profile = Field(default_factory=Profile)
    checkin: CheckIn | None = None
    location: LatLon
    t: datetime | None = None
    blocked: list[str] = Field(default_factory=list)


class LocationRisk(BaseModel):
    ground_elev_m: float | None
    in_corridor: bool
    reach: str | None
    distance_to_river_m: float | None
    water_elev_now_m: float | None
    water_elev_forecast_max_m: float | None
    t_flood_here: datetime | None
    flooded_now: bool
    cut_off_now: bool


class AssessResponse(BaseModel):
    crisis: bool
    hazard: str | None
    triggers: list[str]
    verdict: Verdict
    questions: list[CheckInQuestion]
    risk: LocationRisk | None
    t: datetime
    data_mode: str
