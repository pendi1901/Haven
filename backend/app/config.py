"""Runtime configuration: env vars, regions, gauges, thresholds, poll intervals.

Everything that is a judgment call (thresholds, buffers, margins) lives here so it
can be audited in one place. Values marked "spec §N" come from the build spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
DATA_ROOT = BACKEND_DIR / "data"

# Working projected CRS: NAD83 / UTM zone 17N. It is the native CRS of the USGS 1 m
# 3DEP tiles, so DEM sampling needs no reprojection. Units: meters.
WORK_CRS = "EPSG:26917"
FT_PER_M = 3.280839895


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    airnow_api_key: str = ""
    purpleair_api_key: str = ""
    firms_map_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    nws_user_agent: str = "Haven (team-email@example.com)"
    data_mode: Literal["live", "replay"] = "live"  # default mode; the app can switch per request
    region: str = "asheville"  # default live region when no location is known
    replay_region: str = "asheville"

    # Base URLs (spec §5: keep base URLs in config).
    nwps_base: str = "https://api.water.noaa.gov/nwps/v1"
    nws_base: str = "https://api.weather.gov"
    usgs_water_base: str = "https://api.waterdata.usgs.gov/ogcapi/v0"
    airnow_base: str = "https://www.airnowapi.org"
    purpleair_base: str = "https://api.purpleair.com/v1"
    epa_uv_base: str = "https://data.epa.gov/efservice"
    firms_base: str = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
    quakes_url: str = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
    nhc_current_url: str = "https://www.nhc.noaa.gov/CurrentStorms.json"
    nhc_gis_archive: str = "https://www.nhc.noaa.gov/gis/forecast/archive"
    fema_shelters_url: str = (
        "https://gis.fema.gov/arcgis/rest/services/NSS/OpenShelters/FeatureServer/0/query"
    )
    # DriveNC moved its API in 2026 (see https://drivenc.gov/help/endpoint/event).
    # Leave empty to disable live closures; set to a GeoJSON/JSON events endpoint.
    ncdot_events_url: str = ""
    ncdot_api_key: str = ""
    iem_base: str = "https://mesonet.agron.iastate.edu"
    census_api_key: str = ""

    cors_origins: str = "*"
    warm_replay_cache: bool = True


@lru_cache
def settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GaugeConfig:
    lid: str  # NOAA NWPS location id
    usgs_id: str | None
    short_name: str
    # Replay: stop using readings after this instant (spec §4: BLTN7 readings stop
    # near Helene's peak). None means use every reading the archive has.
    replay_last_reading: datetime | None = None
    # Base-flow stage (ft). Road samples whose lidar elevation is below the water
    # surface at this stage are in the river channel (no road sits below normal
    # river level) and are excluded. None disables the exclusion.
    base_flow_stage_ft: float | None = None


@dataclass(frozen=True)
class ReachConfig:
    """A river reach evaluated by road impact (spec §7.3 step 2).

    The water surface is linearly interpolated by distance along the river between
    `upstream_lid` and the downstream end. The downstream end is either a gauge
    (`downstream_lid`) or the confluence with another reach (`joins_reach`), where
    the water elevation is that reach's interpolated value at the confluence.
    """

    id: str
    river_name: str
    upstream_lid: str
    downstream_lid: str | None = None
    joins_reach: str | None = None
    # (lat, lon) where the reach ends when that is not at the downstream gauge,
    # e.g. a lake inlet whose level is read by a gauge at the dam.
    downstream_at: tuple[float, float] | None = None
    # Unnamed OSM waterway ways that carry this river across gaps between its
    # named ways (culverts, unnamed segments).
    extra_osm_ways: tuple[int, ...] = ()


@dataclass(frozen=True)
class RegionConfig:
    key: str
    name: str
    bbox: tuple[float, float, float, float]  # west, south, east, north (WGS84)
    center: tuple[float, float]  # lat, lon
    timezone: str
    state: str
    uv_zip: str
    gauges: tuple[GaugeConfig, ...]
    reaches: tuple[ReachConfig, ...]
    replay_start: datetime | None = None
    replay_end: datetime | None = None
    replay_step_minutes: int = 15
    replay_event: str | None = None
    replay_nhc_storm: str | None = None  # e.g. "al092024"
    replay_rvf_pils: tuple[str, ...] = ()  # AFOS PILs carrying RFC river forecasts
    replay_rvf_office: str | None = None  # issuing RFC (e.g. KORN = LMRFC)
    replay_wfos: tuple[str, ...] = ()
    demo_places: tuple[dict, ...] = field(default_factory=tuple)
    uv_city: str | None = None  # EPA UV lookup by city when there is no ZIP
    point: bool = False  # built on the fly around a user's location (no overlay data)

    @property
    def data_dir(self) -> Path:
        return DATA_ROOT / ("points" if self.point else "") / self.key

    @property
    def has_overlay(self) -> bool:
        """Prepared terrain + road graph + river reaches (python -m app.prepare)."""
        d = self.data_dir
        return (not self.point and (d / "derived" / "road_index.npz").exists()
                and (d / "dem" / "dem_4m.tif").exists() and (d / "osm" / "rivers.geojson").exists())

    @property
    def has_replay(self) -> bool:
        return (not self.point and self.replay_start is not None
                and (self.data_dir / "replay" / "rvf_forecasts.json").exists())

    def contains(self, lat: float, lon: float) -> bool:
        w, s, e, n = self.bbox
        return s <= lat <= n and w <= lon <= e

    def gauge(self, lid: str) -> GaugeConfig:
        for g in self.gauges:
            if g.lid == lid:
                return g
        raise KeyError(lid)


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


REGIONS: dict[str, RegionConfig] = {
    "asheville": RegionConfig(
        key="asheville",
        name="Asheville, NC (French Broad and lower Swannanoa)",
        # Fletcher to just north of the Asheville gauge, Bent Creek to the lower
        # Swannanoa. Small on purpose (spec §4).
        bbox=(-82.640, 35.405, -82.460, 35.645),
        center=(35.575, -82.555),
        timezone="America/New_York",
        state="NC",
        uv_zip="28801",
        gauges=(
            # Base flow: USGS readings at 2024-09-25 00:00 EDT, before Helene's rain.
            GaugeConfig("FLCN7", "03447687", "French Broad near Fletcher", base_flow_stage_ft=4.68),
            GaugeConfig("AVLN7", "03451500", "French Broad at Asheville", base_flow_stage_ft=2.02),
            GaugeConfig(
                "BLTN7",
                "03451000",
                "Swannanoa at Biltmore",
                replay_last_reading=_utc("2024-09-28T02:30:00+00:00"),
                base_flow_stage_ft=2.82,
            ),
        ),
        reaches=(
            ReachConfig("french_broad", "French Broad River", "FLCN7", downstream_lid="AVLN7"),
            ReachConfig("swannanoa", "Swannanoa River", "BLTN7", joins_reach="french_broad"),
        ),
        replay_start=_utc("2024-09-25T00:00:00-04:00"),
        replay_end=_utc("2024-09-29T00:00:00-04:00"),
        replay_event="Hurricane Helene",
        replay_nhc_storm="al092024",
        replay_rvf_pils=("RVFGSP",),
        replay_rvf_office="KORN",  # NWS Lower Mississippi RFC forecasts the French Broad
        replay_wfos=("GSP",),
        demo_places=(
            {"name": "River Arts District (Riverside Dr)", "lat": 35.5865, "lon": -82.5683},
            {"name": "Biltmore Village", "lat": 35.5668, "lon": -82.5455},
            {"name": "Fletcher, near the river", "lat": 35.4330, "lon": -82.5480},
            {"name": "West Asheville hillside", "lat": 35.5790, "lon": -82.5960},
        ),
    ),
    # Raleigh: Crabtree Creek from Ebenezer Church Rd (official NOAA forecast point)
    # through Glenwood Ave and Anderson Dr to Capitol Blvd, and Walnut Creek.
    # Live mode only (no replay archive is wired up). Base flow: 30-day median
    # stage as of Oct 3, 2026.
    #
    # Walnut Creek has two dams, Lake Johnson and Lake Raleigh, and no gauge reads
    # the creek just below either one, so a stage cannot be interpolated across
    # them. Modeled: Buck Jones Rd down to where the creek enters Lake Johnson
    # (whose level is the downstream end), and Wilmington St down to Sunnybrook
    # Dr. The lake shore itself is not modeled: on the 10 m DEM, roads on the
    # shore and the Avent Ferry Rd causeway sit within a foot of the normal pool,
    # so ordinary lake rises flagged them. Shown on the map but not modeled: the
    # two Cary gauges (NWPS publishes no datum for them) and Trailwood Dr
    # (between the two dams).
    "raleigh": RegionConfig(
        key="raleigh",
        name="Raleigh, NC (Crabtree and Walnut Creeks)",
        bbox=(-78.760, 35.740, -78.580, 35.880),
        center=(35.800, -78.660),
        timezone="America/New_York",
        state="NC",
        uv_zip="27601",
        gauges=(
            GaugeConfig("EBNN7", "0208726005", "Crabtree Creek at Ebenezer Church Rd", base_flow_stage_ft=3.47),
            GaugeConfig("RLHN7", "02087275", "Crabtree Creek at Glenwood Ave", base_flow_stage_ft=4.50),
            GaugeConfig("ADRN7", "0208731190", "Crabtree Creek at Anderson Dr", base_flow_stage_ft=2.05),
            GaugeConfig("CRBN7", "02087324", "Crabtree Creek at Capitol Blvd", base_flow_stage_ft=-0.24),
            GaugeConfig("UCTN7", None, "Walnut Creek above Cary Towne Blvd"),
            GaugeConfig("WCIN7", None, "Walnut Creek above I-40"),
            GaugeConfig("BKJN7", "02087337", "Walnut Creek at Buck Jones Rd", base_flow_stage_ft=0.96),
            # Reports lake elevation (ft NAVD88) on a zero datum, not a stage.
            GaugeConfig("JHSN7", "02087339", "Lake Johnson above dam", base_flow_stage_ft=343.16),
            GaugeConfig("TRLN7", "0208734210", "Walnut Creek at Trailwood Dr"),
            GaugeConfig("WAWN7", "0208734795", "Walnut Creek at S Wilmington St", base_flow_stage_ft=0.55),
            GaugeConfig("WSSN7", "0208735460", "Walnut Creek at S State St", base_flow_stage_ft=1.55),
            GaugeConfig("WRLN7", "02087358", "Walnut Creek at Rose Ln", base_flow_stage_ft=3.95),
            GaugeConfig("WALN7", "02087359", "Walnut Creek at Sunnybrook Dr", base_flow_stage_ft=2.18),
        ),
        reaches=(
            ReachConfig("crabtree_upper", "Crabtree Creek", "EBNN7", downstream_lid="RLHN7"),
            ReachConfig("crabtree_mid", "Crabtree Creek", "RLHN7", downstream_lid="ADRN7"),
            ReachConfig("crabtree_lower", "Crabtree Creek", "ADRN7", downstream_lid="CRBN7"),
            ReachConfig("walnut_buck_jones", "Walnut Creek", "BKJN7", downstream_lid="JHSN7",
                        downstream_at=(35.76800, -78.72230),  # where the creek enters Lake Johnson
                        extra_osm_ways=(1028536915, 1028536916, 1028536917)),
            ReachConfig("walnut_wilmington", "Walnut Creek", "WAWN7", downstream_lid="WSSN7"),
            ReachConfig("walnut_state", "Walnut Creek", "WSSN7", downstream_lid="WRLN7"),
            ReachConfig("walnut_rose", "Walnut Creek", "WRLN7", downstream_lid="WALN7"),
        ),
        demo_places=(
            {"name": "Crabtree Valley Mall area", "lat": 35.8395, "lon": -78.6790},
            {"name": "Downtown Raleigh", "lat": 35.7796, "lon": -78.6382},
        ),
    ),
}


def region() -> RegionConfig:
    key = settings().region
    if key not in REGIONS:
        raise ValueError(f"Unknown REGION={key!r}; choose one of {sorted(REGIONS)}")
    return REGIONS[key]


# ---------------------------------------------------------------------------
# Poll intervals (seconds), spec §5
# ---------------------------------------------------------------------------

POLL_SECONDS = {
    "nwps": 5 * 60,
    "nws_alerts": 2 * 60,
    "nws_forecast": 30 * 60,
    "airnow": 30 * 60,
    "purpleair": 10 * 60,
    "epa_uv": 6 * 3600,
    "firms": 30 * 60,
    "quakes": 2 * 60,
    "nhc": 30 * 60,
    "ncdot": 5 * 60,
    "shelters": 15 * 60,
}

# A value older than this multiple of its poll interval is labeled stale.
STALE_AFTER_INTERVALS = 3

# ---------------------------------------------------------------------------
# Geo / routing parameters
# ---------------------------------------------------------------------------

CORRIDOR_M = 500.0  # spec §7.3 step 3
ROAD_SAMPLE_SPACING_M = 8.0  # DEM sampling step along road edges (DEM is 4 m)
AT_RISK_DEPTH_FT = 0.0  # depth > 0 → at least at_risk (spec §7.3 step 4)
FLOODED_DEPTH_FT = 0.5  # depth > 0.5 ft → flooded
GAUGE_OFFLINE_AFTER_H = 2.0  # spec §7.2 step 4
FLOOR_HEIGHT_M = 3.0  # spec §7.7 step 3
UPPER_FLOOR_MARGIN_M = 1.0
DESTINATION_MARGIN_M = 2.0  # spec §7.5 "high ground" margin
ROUTE_BUFFER_MIN = 15  # spec §7.5
FORD_PENALTY = 5.0
WALK_SPEED_MPS = {"default": 1.3, "kids_or_older": 1.0, "limited_mobility": 0.8}
CAR_CAPACITY = 5
PET_PREFERENCE_SLACK = 0.20  # spec scenario 10
HIGH_GROUND_CANDIDATES = 3
LOCATION_ROAD_RADIUS_M = 300.0  # spec §7.7 step 2 (t_flood_here)

# Crisis triggers, spec §7.6
TRIGGER_GAUGE_RADIUS_KM = 10.0
TRIGGER_FORECAST_HOURS = 12.0
TRIGGER_ROAD_RADIUS_KM = 1.0
TRIGGER_FIRE_KM = 15.0
TRIGGER_NHC_HOURS = 48.0

# Decision windows, spec §9
LEAVE_WINDOW_H = 6.0
SHELTER_WINDOW_H = 12.0
FIRE_LEAVE_KM = 5.0
CHECKIN_TTL_H = 3.0

# ---------------------------------------------------------------------------
# Thresholds, spec §8. Each list is (lower bound inclusive, category, severity).
# ---------------------------------------------------------------------------

AQI_BANDS = [
    (0, "Good", 0),
    (51, "Moderate", 0),
    (101, "Unhealthy for Sensitive Groups", 1),
    (151, "Unhealthy", 2),
    (201, "Very Unhealthy", 3),
    (301, "Hazardous", 4),
]
HEAT_BANDS = [
    (-1000, "None", 0),
    (80, "Caution", 0),
    (90, "Extreme Caution", 1),
    (103, "Danger", 3),
    (125, "Extreme Danger", 4),
]
UV_BANDS = [
    (0, "Low", 0),
    (3, "Moderate", 0),
    (6, "High", 1),
    (8, "Very High", 1),
    (11, "Extreme", 2),
]
RIVER_SEVERITY = {"none": 0, "action": 1, "minor": 2, "moderate": 3, "major": 4}
RIVER_CATEGORIES = ["action", "minor", "moderate", "major"]

TRIGGER_AQI = 151
TRIGGER_AQI_SENSITIVE = 101
TRIGGER_HEAT_INDEX_F = 103


def band(value: float, bands: list[tuple[float, str, int]]) -> tuple[str, int]:
    cat, sev = bands[0][1], bands[0][2]
    for lo, c, s in bands:
        if value >= lo:
            cat, sev = c, s
    return cat, sev
