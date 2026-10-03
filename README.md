# Haven

**Wherever you are, Haven shows what's happening, what official forecasts say is coming, and exactly what to do about it, adjusted to your situation right now.**

WolfHacks 2026 · Center for Geospatial Analytics track.

Haven uses only realtime observations and the forecasts that official sources already publish (NOAA/NWS/NWPS, USGS, EPA, NASA, NHC, FEMA). It does not forecast anything itself. Its own logic is deterministic: thresholds, geospatial overlays (point-in-polygon, elevation comparison), routing, and a rule-based decision engine.

- **Calm mode** — live conditions and official forecast heads-ups, one line of advice each.
- **Crisis mode** — official alert text first, then a verdict (Level 0–5), live check-in, steps, and the least risky route in this data, with in-app navigation.
- **Replay** — Hurricane Helene, Sept 25–29 2024, Asheville, with a time slider. Replay uses the official products *as they were issued* (no data after the selected time is ever used).

---

## Quick start

Requirements: Python 3.11+, Node 20+.

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp ../.env.example ../.env          # set NWS_USER_AGENT to your contact; keys optional
.venv/bin/python -m app.prepare     # one-time: OSM graphs, 4 m lidar DEM, Helene archives (~5 min)
.venv/bin/uvicorn app.main:app --port 8000
```

```bash
cd frontend
npm install
npm run dev                          # http://localhost:5173 (proxies /api to :8000)
```

Production-style single server: `npm run build` in `frontend/`, then the backend serves `frontend/dist` at `http://localhost:8000`.

Live mode: `DATA_MODE=live` in `.env`. Geolocation needs HTTPS on phones (localhost is fine for development).

### Tests

```bash
cd backend && .venv/bin/python -m pytest      # 35 tests: decision scenarios 1-11, M1, M3, M4, re-plan, replay no-leakage
cd frontend && npm test                      # scenario 12 (off-route), snap-to-route, arrival
```

### A two-minute replay demo

Times below are Asheville local time (EDT).

1. Onboarding → **River Arts District (Riverside Dr)** → *Skip setup*.
2. **Thu Sep 26, 4:00 PM** (default). A Tropical Storm Warning and Flood Watch are in effect, and NOAA's latest river forecast has the French Broad at Asheville reaching moderate stage overnight. Haven: **Level 4 · Go to a shelter / center**, because the official forecast floods the location, or every road out of it, around 2 AM Friday. The map shows the estimated flooded area now (dark blue), the area at the forecast crest (light blue), and roads forecast to flood (dashed red).
3. *What to do* → answer the check-in (floor, car, who's with you). Each answer removes an assumption chip.
4. *Simulate walk* → follow mode, turn-by-turn directions, a deadline, proximity alerts, arrival. In the simulator toolbar, **Wander off** drifts ~90 m sideways, so off-route detection fires after 3 fixes and reroutes. **I can't continue this way** blocks the current road and reroutes.
5. Drag the slider to **Fri Sep 27, 10:00 AM**. The River Arts District is cut off: **Shelter in place at the highest point available**, with *No open route found in this data*.
6. Switch to **Biltmore Village**. On Wednesday afternoon it is **Level 1**, with watches only. At **Fri 4:00 AM** it is **Level 5 · Leave now**: the official forecast floods it around 5 AM, so walk 19 min uphill to Saint Marks Church. Start the walk, pause (❚❚) and press **+1 h** twice. The newer gauge data shows the remaining route cut off, and Haven switches to shelter-in-place guidance. The observed rise outran the official forecast, as it did during Helene.
7. **Responders** at Fri 6 PM: about 2,000 people in 14 tracts have no drive path to a hospital. Haven flagged 5 of the 6 NCDOT closures in the corridor.

---

## Architecture

```
backend/app
  config.py            regions, gauges, thresholds, buffers (every judgment call in one place)
  models.py            domain types (spec §6)
  prepare.py           one-time data preparation (python -m app.prepare)
  sources/             one client per external API; poll cache with stale marking + disk snapshots
  hazards/             one module per hazard behind refresh / zones / metrics_at
  geo/                 OSM graphs, DEM, road impact, routing, census
  engine/              crisis triggers, decision ladder, step templates, optional Gemini rephrase
  replay/              Helene archives + "what was known at t" query layer, SHEF parser
  api/                 /api/state, /api/assess, /api/route, /api/responder, /api/stream (SSE)
frontend/src
  pages/               Onboarding, Dashboard (calm), Crisis (+ navigation), Responder
  components/          Map (MapLibre), VerdictCard, MetricCard, CheckIn, TimeSlider, RoutePanel, GaugeChart
  lib/                 api, types, storage (device only), geo, nav (snap/off-route/simulator), store
```

### Road impact (deterministic overlay, spec §7.3)

1. Water elevation at a gauge = NWPS NAVD88 datum + stage. The DEM is USGS 3DEP 1 m lidar (NAVD88 meters) mosaicked to 4 m, so there is one unit conversion and no datum shift.
2. The water surface is interpolated linearly along the OSM river centerline between FLCN7 → AVLN7 (31.2 km), and BLTN7 → the French Broad confluence (2.6 km). Nothing beyond the gauges is evaluated.
3. Road edges are sampled every 8 m within 500 m of the reach. Each sample's water elevation is a fixed linear combination of gauge water elevations, so "now" and every official forecast time are one matrix product (~0.1 s for 7,000 edges and the flood-extent polygon).
4. Depth > 0 ft at risk, > 0.5 ft flooded. Bridges/tunnels skip the DEM and inherit their approaches' worst status; road simplification keeps bridges as separate edges so the riverbed under a bridge never floods it.
5. Samples below the pre-storm base-flow water surface are river channel (no road sits below normal river level) and are excluded. Without this, river-access paths showed "flooded" on a normal day.

Sanity checks (tests): at base flow nothing is flagged; at Helene's peak Riverside Dr, Lyman St, Amboy Rd, Meadow Rd and All Souls Crescent flood, and no flooded edge sits entirely above the highest water surface.

### Routing (spec §7.5, §7.7)

One Dijkstra from the user gives the cost to every candidate destination (equivalent to the virtual-sink formulation; it also yields the backup). Edges are removed when closed, flooded or at risk now, reported blocked, or forecast to flood before arrival + 15 min (iterated until the trip estimate is stable). Fords and river bridges at minor stage or higher cost ×5. Destinations must sit 2 m above the forecast peak water level of their nearest river point and outside every hazard zone. Google Maps handoff adds up to 3 waypoints 50 m past each point where Haven's route leaves the plain fastest route, keeping the ones nearest the hazards.

---

## Data sources (verified Oct 3, 2026)

| Source | Use | Status |
| --- | --- | --- |
| NWPS gauges + stageflow | thresholds, NAVD88 datum, observed stage, **official river forecast** | works |
| NWS alerts (`area=NC`, zone geometry for zone-based alerts) | warnings/watches, verbatim text | works |
| NWS points → hourly + grid forecast | heat index (NWS grid / Rothfusz), wind direction | works |
| EPA Envirofacts UV hourly | UV peak and high hours | works |
| USGS earthquakes (day feed) | quakes within 200 km | works |
| NHC CurrentStorms + GIS cone/track | cone and 48 h TS conditions | works |
| FEMA open shelters (NSS) | official open shelters, pet/ADA flags | works |
| AirNow, NASA FIRMS, PurpleAir | AQI + forecast, fire detections | need keys; show "Add … key to enable" |
| NCDOT/DriveNC events | live closures | API moved in 2026; `NCDOT_EVENTS_URL` configurable, disabled until set |
| USGS 3DEP 1 m lidar COGs (S3) | DEM (4 m mosaic via COG overviews) | works |
| OpenStreetMap via OSMnx | drive/walk graphs, rivers, refuge POIs | works |
| TIGERweb 2020 tracts | responder population | works (ACS fields need `CENSUS_API_KEY`) |

**Replay archives** (`python -m app.prepare --steps replay`):

| Archive | Content |
| --- | --- |
| USGS Water Data API | 15-min gage height, 3 gauges |
| IEM NWS text archive, `RVFGSP` from LMRFC (KORN) | the official river forecasts issued during Helene (SHEF), parsed with `replay/shef.py` |
| IEM VTEC archive | storm-based warning polygons + zone watches/warnings, with the original product text |
| NHC GIS archive `al092024_5day_NNN` | forecast cones and tracks per advisory |
| NCDOT incident lines, WNC Post-Helene Conditions Map (Oct 8, 2024) | **accuracy scoring only**, never routing |

---

## Deviations from the spec (and why)

- **Official forecasts in replay.** The spec expected none ("not easily retrievable"). The NWS LMRFC river forecasts issued during Helene are archived by IEM, so replay uses them, always the latest product issued at or before `t`. They underestimated the Asheville crest (21.0 ft forecast vs 24.82 ft observed), which Haven shows faithfully.
- **Elevation source.** The 3DEP dynamic ImageServer (used by py3dep) returned 502/IPv6 errors from this network; Haven reads the same USGS 1 m lidar from the public S3 cloud-optimized GeoTIFFs instead.
- **Accessible destinations.** Only 8 of 206 OSM candidate sites carry an accessibility tag. For limited mobility Haven chooses an accessible site when it costs at most 2× (or +10 min) the nearest; otherwise it takes the nearest and says the site isn't marked accessible.
- **Trigger 3 ("a road within 1 km")** counts drive-graph roads only, so a waterside footpath can't start crisis mode on its own.
- **Gauge display names** use short names ("French Broad at Asheville") instead of the NWPS long names.
- **Route start.** Instead of snapping to the single nearest road node, which can start a dry user on a flooded street, a route may begin at any of the 8 nearest road nodes within 120 m. A node counts only if the straight walk to it doesn't cross the current flood extent.
- **Re-plan demo.** On Helene's data a forecast-aware walking route almost never breaks mid-trip: trips are 10–20 min, and edges forecast to flood before arrival + 15 min are already excluded. The simulator's **+1 h** control advances the replay clock (new NOAA data) to exercise the re-plan path; `test_replan_check_flags_route_when_data_changes` covers it.
- **Not implemented:** the optional National Water Model reference flow for offline gauges (spec §7.2 step 4), and live closures until a DriveNC endpoint is configured.

## Limitations

- Haven is decision support layered on official alerts, never a replacement. Wording is "least risky route in this data", never "safe route".
- The flood extent and road statuses are a terrain overlay of official river levels; they do not model creeks, flash flooding, landslides or debris (many Helene closures were landslides). Swannanoa River Road, upstream of the Biltmore gauge, is outside the evaluated reach.
- A web app tracks location only while open in the foreground; the Google Maps handoff covers backgrounded navigation.
- No archived AQI, heat, UV or fire data are loaded for replay; those cards say so.
- OSM data quality applies (e.g. an urgent care tagged `amenity=hospital`).

## Privacy

The profile and check-in live in the browser's local storage. Requests carry only the location and the fields needed for one decision. Nothing is persisted server-side, and location is never logged.
