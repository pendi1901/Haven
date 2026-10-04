# Haven

**Wherever you are, Haven shows what's happening, what official forecasts say is coming, and exactly what to do about it, adjusted to your situation right now.**

**Live: [safehaven.casa](https://safehaven.casa)** · WolfHacks 2026 · Center for Geospatial Analytics track.

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

Production-style single server: `npm run build` in `frontend/`, then the backend serves the app at `http://localhost:8000`.

### Deployment: https://safehaven.casa

Haven is self-hosted. The production app runs in Docker on a team member's laptop and is published at **[safehaven.casa](https://safehaven.casa)** through a Cloudflare Tunnel.

```
Visitor ──https──▶ Cloudflare (DNS + TLS for safehaven.casa)
                        │  Cloudflare Tunnel (outbound connection from the laptop;
                        ▼  no open ports, works on campus or event Wi-Fi)
           ┌──────────── laptop · Docker network "haven-net" ────────────┐
           │  cloudflared  ──▶  haven  (FastAPI: React app + /api, :8000)  │
           └──────────────────────────────────────────────────────────────┘
```

| Piece | What we use |
| --- | --- |
| Domain | `safehaven.casa`, a `.casa` domain from GoDaddy Registry, registered through Porkbun |
| DNS and HTTPS | Cloudflare (free plan); the domain's nameservers point to Cloudflare |
| Ingress | Cloudflare Tunnel (`cloudflared`), so the laptop never accepts inbound connections |
| App | One Docker container from the root `Dockerfile`: the React build plus FastAPI, serving both the frontend and `/api` |
| Accounts | Supabase (Google sign-in), configured at build time, see below |

The prepared Asheville and Raleigh datasets are baked into the image, so no `app.prepare` step is needed on the server.

#### 1. Build and run Haven

Supabase settings are inlined into the frontend when it is built. The `Dockerfile` defaults its `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` build args to the team's project, so a plain build has sign-in on. Both values are public by design. To use another project, pass `--build-arg` for both; pass them empty to build with accounts off. Putting them in `.env` does nothing: that file is read when the server starts, after the frontend is built.

```bash
docker network create haven-net

docker build -t haven .

docker run -d --name haven --network haven-net --restart unless-stopped \
  -p 8000:8000 --env-file .env haven
```

Check `http://localhost:8000` on the laptop before going further (health check: `/api/health`). Runtime settings come from `.env` (see the table below). For `--env-file`, use plain `KEY=value` lines: unlike Python dotenv, Docker does not strip surrounding quotes or inline comments.

#### 2. Point the domain at Cloudflare (one time)

1. Cloudflare dashboard → **Add a domain** → `safehaven.casa` → Free plan.
2. Porkbun → Domain Management → `safehaven.casa` → **Authoritative Nameservers** → replace Porkbun's with the two Cloudflare nameservers. Remove any DNSSEC records first.
3. Wait until Cloudflare marks the domain **Active**.

#### 3. Create the tunnel (one time)

1. Cloudflare → **Zero Trust** → Networks → **Tunnels** → Create a tunnel → **Cloudflared**, named `haven`.
2. Run the connector on the laptop, on the same Docker network as Haven. The token is a secret: never commit or share it.
   ```bash
   docker run -d --name cloudflared --network haven-net --restart unless-stopped \
     cloudflare/cloudflared:latest tunnel --no-autoupdate run --token <TUNNEL_TOKEN>
   ```
3. Add two **public hostnames**, both with service **HTTP** → `haven:8000`:
   - `safehaven.casa`
   - `www.safehaven.casa`

   Cloudflare creates the DNS records for them.

#### 4. Allow the domain for sign-in (one time)

- Supabase → Authentication → URL Configuration → **Redirect URLs**: add `https://safehaven.casa/**` and `https://www.safehaven.casa/**`. Keep `http://localhost:5173/**` for development.
- Google Auth Platform → Clients → the web client → **Authorized JavaScript origins**: add `https://safehaven.casa` and `https://www.safehaven.casa`. The redirect URI stays Supabase's callback.
- While the Google app is in Testing mode, only accounts listed under **Audience → Test users** can sign in.

#### Updating the live site

```bash
git pull
docker build -t haven .
docker rm -f haven
docker run -d --name haven --network haven-net --restart unless-stopped \
  -p 8000:8000 --env-file .env haven
```

The tunnel keeps running; it reconnects to the new container by name.

#### Runtime settings (`.env`)

| Variable | Value |
| --- | --- |
| `NWS_USER_AGENT` | `Haven (your-real-contact@example.com)`: NWS requires a contact |
| `DATA_MODE` | `live` (image default; the UI can switch to replay) |
| `REGION` | `asheville` (image default; live requests select the region by location) |
| `WARM_REPLAY_CACHE` | `false` (image default; computes replay timesteps on demand) |
| `AIRNOW_API_KEY` | Your key, to enable AirNow |
| `FIRMS_MAP_KEY` | Your key, to enable NASA fire detections |
| `PURPLEAIR_API_KEY` | Optional sensor fallback |
| `GEMINI_API_KEY` | Optional verdict rephrasing |
| `NCDOT_EVENTS_URL`, `NCDOT_API_KEY` | Optional live closure endpoint and credentials |
| `CENSUS_API_KEY` | Optional; only adds ACS fields when rerunning census preparation |

#### Operating notes

- **The laptop is the server.** Keep it plugged in, awake and online (on macOS, `caffeinate -dims`). If it sleeps or goes offline, the site is down; both containers restart on their own when Docker comes back.
- **One worker.** Each additional worker duplicates the in-memory terrain, graphs and polling jobs.
- **No persistent volume needed.** The bundled data lives in the image; runtime snapshots and caches rebuild after a restart. Don't mount an empty volume over `/app/backend/data`, which would hide the datasets.

### Live anywhere, plus the Helene replay

One server does everything. The app opens in **Live** mode at your current location (the browser asks for permission; geolocation needs HTTPS on phones, and localhost is fine for development). **Helene replay** is a toggle in the header.

What live mode gives you depends on where you are:

| Where | What works |
| --- | --- |
| Anywhere NWS covers (the US) | Official NWS alerts with verbatim text, the NWS hourly forecast and heat index, EPA UV, the NOAA river gauges within 15 km and their official forecasts, quakes, NHC cones, FEMA open shelters, AirNow and FIRMS if keys are set, and the decision engine. Without terrain data Haven never claims flooding won't reach you; it says it can't tell. |
| Inside a prepared region | All of the above, plus the road-flood overlay, flood extent, least-risky routing, navigation and the responder view. The server picks the region automatically from your location. |
| Outside the US | A clear "NWS doesn't cover this location" message. |

Prepared regions (`backend/app/config.py`):

| Region | Rivers / gauges | Terrain | Replay |
| --- | --- | --- | --- |
| `asheville` | French Broad FLCN7 → AVLN7, Swannanoa BLTN7 → confluence | USGS 3DEP 1 m lidar | Hurricane Helene |
| `raleigh` | Crabtree Creek EBNN7 → RLHN7 → ADRN7 → CRBN7; Walnut Creek BKJN7 → Lake Johnson (JHSN7 lake level) and WAWN7 → WSSN7 → WRLN7 → WALN7. UCTN7, WCIN7 and TRLN7 are shown but not modeled (no datum, or between the Lake Johnson and Lake Raleigh dams). Lake Raleigh has no gauge | USGS 3DEP 1/3 arc-second (~10 m); no 1 m tiles are staged there | live only |

Prepare a region once, and it is picked up on the next server start:

```bash
REGION=raleigh .venv/bin/python -m app.prepare
```

Live and replay keep separate saved locations, so a replay spot in Asheville never becomes your live location.

**If "Use my current location" fails:** the app first asks for a normal-accuracy fix (Wi-Fi based), then retries with high accuracy, and it says which step failed. On a Mac, "allowed but no position" almost always means macOS Location Services is off for the browser itself: turn it on in System Settings → Privacy & Security → Location Services. You can always search for an address or place instead (OpenStreetMap Nominatim, US only, proxied by the backend at one request per second) or tap the map.

### Optional accounts: Google sign-in and profile sync

Without this, Haven works as before: the onboarding profile stays in the browser. With it, a signed-in user's profile is saved to their account and restored on any device. Signing in is never required.

Setup (Supabase free tier):

1. Create a Supabase project. Run [`supabase/migrations/20261003000000_profiles.sql`](supabase/migrations/20261003000000_profiles.sql) in the SQL editor. It creates `public.profiles` with row level security, so each signed-in user can read and write only their own row and anonymous visitors get nothing.
2. Google Cloud Console → APIs & Services → Credentials → OAuth client ID (Web application). Authorized redirect URI: `https://<project-ref>.supabase.co/auth/v1/callback`.
3. Supabase → Authentication → Providers → Google: enable it and paste the client ID and secret.
4. Supabase → Authentication → URL Configuration: set the Site URL to `http://localhost:5173` and add `http://localhost:5173/**` to the redirect URLs (plus your deployed origin, if any).
5. `cp frontend/.env.example frontend/.env` and fill in the project URL and the **publishable** key (Project Settings → API Keys). Never put the secret key in the frontend.

Hosted builds: the `Dockerfile` defaults to the team's project; override both values with `--build-arg` for another one. Then allow the production domain in Supabase and Google. See [Deployment](#deployment-httpssafehavencasa).

How it works:

- The browser talks to Supabase directly (`supabase-js`); the FastAPI backend is unchanged and still stores nothing.
- On sign-in, the more recent copy wins: an account with no profile yet takes this device's, and a fresh device restores the account's.
- Household members (including limited mobility) and health answers sync only if the user ticks "Also sync who lives with you and health answers" in the account menu. Off by default; turning it off removes them from the account.
- Check-in answers and the active route are never synced: they expire within hours.
- Signing out removes the profile, check-in, active route and live location from the device (useful on shared computers) and returns to the welcome screen. The account keeps its copy; household and health answers that weren't synced are gone.

### Tests

```bash
cd backend && .venv/bin/python -m pytest      # 35 tests: decision scenarios 1-11, M1, M3, M4, re-plan, replay no-leakage
cd frontend && npm test                      # scenario 12 (off-route), snap-to-route, arrival, profile sync rules
```

### Simulated Walnut Creek flood (Raleigh)

A made-up storm for demos, selectable in the header as **Walnut Creek simulation**. It runs through the same replay
machinery as Helene (time slider, verdicts, routing, simulated walk), but its gauge readings, river forecasts and
alerts are generated by `python -m app.replay.simulate` into `backend/data/raleigh/replay/`. The app labels all of it
as simulated: a banner on every page, "Simulated" badges on alerts, and "simulated forecast" in verdicts. None of the
Walnut Creek gauges has an official NOAA forecast in reality. Rerun the generator after editing it;
`tests/test_simulation.py` checks the files match it and that the demo story below holds.

1. Pick **Walnut Creek simulation**, then **Sunnybrook Rd at Walnut Creek**. **Tue 9:00 PM** (default): a Flood Watch is
   out and the forecast floods the area overnight: **Level 5 · Leave now**, a 14-minute walk to higher ground.
2. Drag to **Wed 1:00 AM**: every road out is cut off, so the advice becomes **shelter in place** (Wake County Emergency
   Management).
3. **Rose Ln at Walnut Creek** at 9 PM is **Stay put and monitor**; at **11 PM** a newer forecast raises the crest and it
   becomes **Leave now**. Start the walk, pause, and press **+1 h** a few times: Rose Lane floods and Haven reroutes.

### A two-minute replay demo

Times below are Asheville local time (EDT).

1. Switch the header toggle to **Helene replay**, then pick **River Arts District (Riverside Dr)**. (Switch back to **Live** any time to see your own location.)
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
| IEM VTEC archive | warning polygons and zone watches/warnings with the original product text, plus 15-minute snapshots of every event in effect for the region's counties and zones. "In effect at t" comes from these snapshots, which include extensions and cancellations. |
| NHC GIS archive `al092024_5day_NNN` | forecast cones and tracks per advisory |
| NCDOT incident lines, WNC Post-Helene Conditions Map (Oct 8, 2024) | **accuracy scoring only**, never routing |

---

## Deviations from the spec (and why)

- **Official forecasts in replay.** The spec expected none ("not easily retrievable"). The NWS LMRFC river forecasts issued during Helene are archived by IEM, so replay uses them, always the latest product issued at or before `t`. They underestimated the Asheville crest (21.0 ft forecast vs 24.82 ft observed), which Haven shows faithfully.
- **Elevation source.** The 3DEP dynamic ImageServer (used by py3dep) returned 502 and IPv6 errors from this network. Haven reads the same USGS 3DEP data from the public S3 cloud-optimized GeoTIFFs instead. It uses 1 m lidar where tiles are staged, otherwise the 1/3 arc-second DEM, and the UI names which one is in use.
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

The profile and check-in live in the browser's local storage. Requests carry only the location and the fields needed for one decision. Haven's server persists nothing, and location is never logged.

If a user chooses to sign in (optional), their profile is also saved to their own row in Supabase, readable only by them. Household and health answers are excluded unless they opt in, and check-ins are never synced.

## AI usage

Per the WolfHacks rules, this section discloses how AI was used to build Haven.

- **Tool:** [Claude Code](https://claude.com/claude-code) (Anthropic) was the only AI coding tool used.
- **Planning:** the team used Claude to research the event's tracks and the public data sources, and to draft the project idea and the build spec (data sources, decision rules, milestones and scenario tests).
- **Code:** the team gave that spec to Claude Code, which wrote most of the backend, frontend and tests. Team members reviewed the output, ran it, and directed the fixes.
- **Setup and checks:** Claude Code was also used to set up the project on team members' machines, verify the external APIs and run the test suites.
- **In the app:** Haven itself makes no AI predictions. Every forecast comes from an official source (NOAA, NWS, USGS, EPA, NASA, NHC). If `GEMINI_API_KEY` is set, Google Gemini only rewords the decision engine's existing output, and Haven falls back to its own template text when Gemini is unavailable.

The team is responsible for the code, the data choices and every claim the app makes.
