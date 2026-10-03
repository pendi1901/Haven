import maplibregl, { type GeoJSONSource, type LngLatBoundsLike, type Map as MLMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";
import { CATEGORY_COLOR, CATEGORY_LABEL, dayClock } from "../lib/format";
import { splitAt, type XY } from "../lib/geo";
import type { GaugeStatus, LatLon, Meta, Route, StateResponse } from "../lib/types";

const STYLE = "https://tiles.openfreemap.org/styles/positron";
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
// Dry now but forecast (official NOAA forecast + terrain) to flood.
const FORECAST_ONLY: maplibregl.ExpressionSpecification =
  ["all", ["==", ["get", "status"], "dry"], ["to-boolean", ["get", "first_flood_time_forecast"]]];

export interface UserFix { lon: number; lat: number; accuracy: number; heading: number | null }

interface Props {
  state: StateResponse | null;
  meta: Meta | null;
  route?: Route | null;
  backup?: Route | null;
  showBackup?: boolean;
  traveled?: number;
  user?: UserFix | null;
  location?: LatLon | null;
  follow?: boolean;
  onUserPan?: () => void;
  picking?: boolean;
  onPick?: (p: LatLon) => void;
  onGauge?: (g: GaugeStatus) => void;
  overlay?: GeoJSON.FeatureCollection | null; // responder cut-off areas
  points?: { lat: number; lon: number; label: string }[];
  fit?: "region" | "route" | "location";
  liveGeolocate?: boolean;
  onGeolocate?: (fix: UserFix) => void;
  className?: string;
}

function zonesFC(state: StateResponse | null, pred: (z: StateResponse["zones"][number]) => boolean): GeoJSON.FeatureCollection {
  if (!state) return EMPTY;
  return {
    type: "FeatureCollection",
    features: state.zones.filter(pred).map((z) => ({
      type: "Feature", geometry: z.geometry,
      properties: { id: z.id, severity: z.severity, event: z.event ?? z.hazard, headline: z.headline ?? z.reason, warning: z.is_warning },
    })),
  };
}

export default function Map(props: Props) {
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const [ready, setReady] = useState(false);
  const locMarker = useRef<maplibregl.Marker | null>(null);
  const destMarker = useRef<maplibregl.Marker | null>(null);
  const propsRef = useRef(props);
  propsRef.current = props;

  // -- init ------------------------------------------------------------------
  useEffect(() => {
    if (!el.current) return;
    setReady(false); // a rebuilt map must reload sources before effects push data
    const bbox = props.meta?.region.bbox;
    // Construct with center/zoom and fit bounds after load: passing `bounds` while the
    // container is still being laid out makes MapLibre fail to invert its matrix.
    const map = new maplibregl.Map({
      container: el.current,
      style: STYLE,
      center: bbox ? [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2] : [-82.555, 35.575],
      zoom: 11,
      attributionControl: { compact: true },
      fadeDuration: 0,
    });
    if (bbox) map.once("load", () => {
      if (!propsRef.current.fit) map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]] as LngLatBoundsLike, { padding: 20, duration: 0 });
    });
    const ro = new ResizeObserver(() => map.resize());
    ro.observe(el.current);
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: true, visualizePitch: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-left");

    map.on("load", () => {
      const src = (id: string) => map.addSource(id, { type: "geojson", data: EMPTY });
      ["alerts", "cone", "extent-now", "extent-fc", "reaches", "roads", "route-main", "route-traveled",
        "route-backup", "gauges", "fires", "user", "overlay", "points"].forEach(src);

      map.addLayer({ id: "overlay-fill", type: "fill", source: "overlay",
        paint: { "fill-color": "#b91c1c", "fill-opacity": ["interpolate", ["linear"], ["get", "share_cut_off"], 0, 0.08, 0.5, 0.45] } });
      map.addLayer({ id: "overlay-line", type: "line", source: "overlay", paint: { "line-color": "#7f1d1d", "line-width": 1 } });
      map.addLayer({ id: "alerts-fill", type: "fill", source: "alerts",
        paint: { "fill-color": ["case", [">=", ["get", "severity"], 3], "#dc2626", "#ca8a04"], "fill-opacity": 0.06 } });
      map.addLayer({ id: "alerts-line", type: "line", source: "alerts",
        paint: { "line-color": ["case", [">=", ["get", "severity"], 3], "#dc2626", "#ca8a04"], "line-width": 1.5, "line-opacity": 0.6,
          "line-dasharray": [3, 2] } });
      map.addLayer({ id: "cone-line", type: "line", source: "cone", paint: { "line-color": "#475569", "line-width": 1.5, "line-dasharray": [1, 2] } });
      map.addLayer({ id: "extent-fc-fill", type: "fill", source: "extent-fc", paint: { "fill-color": "#38bdf8", "fill-opacity": 0.16 } });
      map.addLayer({ id: "extent-fc-line", type: "line", source: "extent-fc", paint: { "line-color": "#0284c7", "line-width": 1, "line-dasharray": [2, 2] } });
      map.addLayer({ id: "extent-now-fill", type: "fill", source: "extent-now", paint: { "fill-color": "#1d4ed8", "fill-opacity": 0.35 } });
      map.addLayer({ id: "reaches", type: "line", source: "reaches", paint: { "line-color": "#0369a1", "line-width": 1, "line-opacity": 0.35 } });

      // Roads: forecast-flooded = dashed red outline; at_risk amber; flooded red; closed dark red dashed.
      map.addLayer({ id: "roads-fc", type: "line", source: "roads",
        filter: FORECAST_ONLY,
        paint: { "line-color": "#dc2626", "line-width": ["interpolate", ["linear"], ["zoom"], 11, 1.5, 16, 4], "line-dasharray": [2, 1.5], "line-opacity": 0.85 } });
      map.addLayer({ id: "roads-risk", type: "line", source: "roads", filter: ["==", ["get", "status"], "at_risk"],
        paint: { "line-color": "#f59e0b", "line-width": ["interpolate", ["linear"], ["zoom"], 11, 2, 16, 6] } });
      map.addLayer({ id: "roads-flooded", type: "line", source: "roads", filter: ["==", ["get", "status"], "flooded"],
        paint: { "line-color": "#dc2626", "line-width": ["interpolate", ["linear"], ["zoom"], 11, 2.5, 16, 7] } });
      map.addLayer({ id: "roads-closed", type: "line", source: "roads", filter: ["==", ["get", "status"], "closed"],
        paint: { "line-color": "#7f1d1d", "line-width": ["interpolate", ["linear"], ["zoom"], 11, 2.5, 16, 7], "line-dasharray": [1, 1] } });

      map.addLayer({ id: "route-backup", type: "line", source: "route-backup",
        paint: { "line-color": "#334155", "line-width": 3, "line-dasharray": [2, 1.5], "line-opacity": 0.8 } });
      map.addLayer({ id: "route-casing", type: "line", source: "route-main", layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#ffffff", "line-width": 11 } });
      map.addLayer({ id: "route-main", type: "line", source: "route-main", layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#1d4ed8", "line-width": 6 } });
      map.addLayer({ id: "route-traveled", type: "line", source: "route-traveled", layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#94a3b8", "line-width": 6 } });

      map.addLayer({ id: "fires", type: "circle", source: "fires",
        paint: { "circle-radius": 5, "circle-color": "#ea580c", "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } });
      map.addLayer({ id: "points", type: "circle", source: "points",
        paint: { "circle-radius": 6, "circle-color": "#0f172a", "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
      map.addLayer({ id: "gauges", type: "circle", source: "gauges",
        paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 7, 15, 11], "circle-color": ["get", "color"],
          "circle-stroke-color": ["case", ["get", "online"], "#0f172a", "#94a3b8"], "circle-stroke-width": 2.5 } });
      map.addLayer({ id: "gauge-labels", type: "symbol", source: "gauges",
        layout: { "text-field": ["get", "label"], "text-size": 12, "text-offset": [0, 1.4], "text-anchor": "top",
          "text-font": ["Noto Sans Bold"], "text-allow-overlap": false },
        paint: { "text-color": "#0f172a", "text-halo-color": "#fff", "text-halo-width": 1.5 } });
      map.addLayer({ id: "user-acc", type: "circle", source: "user",
        paint: { "circle-radius": ["get", "r"], "circle-color": "#2563eb", "circle-opacity": 0.12 } });
      map.addLayer({ id: "user-dot", type: "circle", source: "user",
        paint: { "circle-radius": 8, "circle-color": "#2563eb", "circle-stroke-color": "#fff", "circle-stroke-width": 3 } });

      // Interactions
      const popup = new maplibregl.Popup({ closeButton: true, maxWidth: "280px" });
      map.on("click", "roads-flooded", (e) => roadPopup(e, popup, map));
      map.on("click", "roads-risk", (e) => roadPopup(e, popup, map));
      map.on("click", "roads-fc", (e) => roadPopup(e, popup, map));
      map.on("click", "roads-closed", (e) => roadPopup(e, popup, map));
      map.on("click", "gauges", (e) => {
        const lid = e.features?.[0]?.properties?.lid;
        const g = propsRef.current.state?.gauges.find((x) => x.lid === lid);
        if (g) propsRef.current.onGauge?.(g);
      });
      map.on("click", "alerts-fill", (e) => {
        if (propsRef.current.picking) return;
        const hit = map.queryRenderedFeatures(e.point, { layers: ["gauges", "roads-flooded", "roads-risk", "roads-fc", "roads-closed"] });
        if (hit.length) return;
        const fs = e.features ?? [];
        if (!fs.length) return;
        const html = fs.slice(0, 4).map((f) => `<div class="pb-1"><b>${esc(f.properties?.event)}</b><br/><span class="text-xs text-slate-600">${esc(f.properties?.headline)}</span></div>`).join("");
        popup.setLngLat(e.lngLat).setHTML(html).addTo(map);
      });
      map.on("click", "overlay-fill", (e) => {
        const p = e.features?.[0]?.properties;
        if (!p) return;
        popup.setLngLat(e.lngLat).setHTML(`<b>${esc(p.name)}</b><br/>${Math.round(p.share_cut_off * 100)}% of road nodes cut off<br/>≈ ${p.population_cut_off_est} people`).addTo(map);
      });
      for (const l of ["gauges", "roads-flooded", "roads-risk", "roads-fc", "roads-closed", "overlay-fill"]) {
        map.on("mouseenter", l, () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", l, () => (map.getCanvas().style.cursor = propsRef.current.picking ? "crosshair" : ""));
      }
      map.on("click", (e) => {
        if (propsRef.current.picking) propsRef.current.onPick?.({ lat: e.lngLat.lat, lon: e.lngLat.lng });
      });
      map.on("dragstart", () => propsRef.current.onUserPan?.());
      setReady(true);
    });

    if (props.liveGeolocate) {
      const geo = new maplibregl.GeolocateControl({
        positionOptions: { enableHighAccuracy: true }, trackUserLocation: true, showAccuracyCircle: true,
      });
      map.addControl(geo, "top-right");
      geo.on("geolocate", (p: GeolocationPosition) =>
        propsRef.current.onGeolocate?.({ lon: p.coords.longitude, lat: p.coords.latitude, accuracy: p.coords.accuracy, heading: p.coords.heading }));
      map.on("load", () => geo.trigger());
    }
    return () => {
      ro.disconnect();
      locMarker.current?.remove();
      locMarker.current = null;
      destMarker.current?.remove();
      destMarker.current = null;
      map.remove();
      mapRef.current = null;
    };
  }, [props.meta?.region.key, props.liveGeolocate]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (id: string, data: GeoJSON.FeatureCollection | GeoJSON.Feature) => {
    const s = mapRef.current?.getSource(id) as GeoJSONSource | undefined;
    s?.setData(data);
  };

  // -- data ------------------------------------------------------------------
  useEffect(() => {
    if (!ready) return;
    const s = props.state;
    set("roads", s?.roads ?? EMPTY);
    set("alerts", zonesFC(s, (z) => z.hazard === "nws_alert"));
    set("cone", zonesFC(s, (z) => z.hazard === "hurricane"));
    set("extent-now", zonesFC(s, (z) => z.hazard === "river_flood" && z.layer === "now"));
    set("extent-fc", zonesFC(s, (z) => z.hazard === "river_flood" && z.layer === "forecast"));
    set("gauges", {
      type: "FeatureCollection",
      features: (s?.gauges ?? []).map((g) => ({
        type: "Feature", geometry: { type: "Point", coordinates: [g.lon, g.lat] },
        properties: {
          lid: g.lid, online: g.online, color: g.online ? CATEGORY_COLOR[g.category_now] : "#cbd5e1",
          label: `${g.short_name.replace("French Broad ", "").replace("Swannanoa ", "")} ${g.observed_stage_ft != null ? g.observed_stage_ft.toFixed(1) + " ft" : ""}`,
        },
      })),
    });
    set("fires", { type: "FeatureCollection", features: (s?.fires ?? []).map((f) => ({ type: "Feature", geometry: { type: "Point", coordinates: [f.lon, f.lat] }, properties: {} })) });
  }, [ready, props.state]);

  useEffect(() => {
    if (!ready || !props.meta) return;
    set("reaches", { type: "FeatureCollection", features: props.meta.reaches.map((r) => ({ type: "Feature", geometry: r.geometry, properties: { id: r.id } })) });
  }, [ready, props.meta]);

  useEffect(() => {
    if (!ready) return;
    set("overlay", props.overlay ?? EMPTY);
    set("points", { type: "FeatureCollection", features: (props.points ?? []).map((p) => ({ type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] }, properties: { label: p.label } })) });
  }, [ready, props.overlay, props.points]);

  // Route: main line split into traveled (dimmed) + remaining.
  useEffect(() => {
    if (!ready) return;
    const r = props.route;
    if (!r) {
      set("route-main", EMPTY);
      set("route-traveled", EMPTY);
    } else {
      const line = r.geometry.coordinates as XY[];
      if (props.traveled && props.traveled > 0) {
        const [done, rest] = splitAt(line, props.traveled);
        set("route-traveled", { type: "Feature", geometry: { type: "LineString", coordinates: done }, properties: {} });
        set("route-main", { type: "Feature", geometry: { type: "LineString", coordinates: rest }, properties: {} });
      } else {
        set("route-traveled", EMPTY);
        set("route-main", { type: "Feature", geometry: r.geometry, properties: {} });
      }
    }
    set("route-backup", props.showBackup && props.backup ? { type: "Feature", geometry: props.backup.geometry, properties: {} } : EMPTY);
  }, [ready, props.route, props.backup, props.showBackup, props.traveled]);

  // Destination marker
  useEffect(() => {
    const map = mapRef.current;
    destMarker.current?.remove();
    destMarker.current = null;
    if (!ready || !map || !props.route) return;
    const d = props.route.destination;
    const node = document.createElement("div");
    node.className = "haven-dest";
    node.innerHTML = `<div class="haven-dest-pin"><span>${d.kind === "open_shelter" ? "⛑" : d.kind === "hospital" ? "✚" : d.kind === "high_ground" ? "▲" : "⌂"}</span></div>`;
    destMarker.current = new maplibregl.Marker({ element: node, anchor: "bottom" })
      .setLngLat([d.lon, d.lat])
      .setPopup(new maplibregl.Popup({ offset: 24 }).setHTML(`<b>${esc(d.name)}</b><br/><span class="text-xs">${esc(d.label)}</span>`))
      .addTo(map);
  }, [ready, props.route]);

  // Static location pin (when not navigating)
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    if (!props.location || props.user) {
      locMarker.current?.remove();
      locMarker.current = null;
      return;
    }
    if (!locMarker.current) {
      const node = document.createElement("div");
      node.className = "haven-you";
      node.innerHTML = `<div class="haven-you-dot"></div>`;
      locMarker.current = new maplibregl.Marker({ element: node }).setLngLat([props.location.lon, props.location.lat]).addTo(map);
    } else {
      locMarker.current.setLngLat([props.location.lon, props.location.lat]);
    }
  }, [ready, props.location, props.user]);

  // Simulated / tracked user dot + follow mode
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const u = props.user;
    if (!u) {
      set("user", EMPTY);
      return;
    }
    const metersPerPx = (156543.03 * Math.cos((u.lat * Math.PI) / 180)) / 2 ** map.getZoom();
    set("user", { type: "Feature", geometry: { type: "Point", coordinates: [u.lon, u.lat] }, properties: { r: Math.max(8, u.accuracy / metersPerPx) } });
    if (props.follow) map.easeTo({ center: [u.lon, u.lat], zoom: Math.max(map.getZoom(), 15.5), duration: 400 });
  }, [ready, props.user, props.follow]);

  // Camera
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    if (props.fit === "route" && props.route) {
      const cs = props.route.geometry.coordinates as XY[];
      const b = cs.reduce((acc, c) => acc.extend(c as [number, number]), new maplibregl.LngLatBounds(cs[0] as [number, number], cs[0] as [number, number]));
      map.fitBounds(b, { padding: { top: 60, bottom: 60, left: 40, right: 40 }, maxZoom: 16, duration: 600 });
    } else if (props.fit === "location" && props.location) {
      map.easeTo({ center: [props.location.lon, props.location.lat], zoom: Math.max(map.getZoom(), 14), duration: 600 });
    }
  }, [ready, props.fit, props.route?.geometry, props.location?.lat, props.location?.lon]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const map = mapRef.current;
    if (map) map.getCanvas().style.cursor = props.picking ? "crosshair" : "";
  }, [props.picking]);

  return <div ref={el} className={props.className ?? "h-full w-full"} role="region" aria-label="Map of hazards and routes" />;
}

function esc(s: unknown): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

function roadPopup(e: maplibregl.MapLayerMouseEvent, popup: maplibregl.Popup, map: MLMap) {
  const p = e.features?.[0]?.properties;
  if (!p) return;
  const status = { dry: "Forecast to flood", at_risk: "At risk now", flooded: "Flooded now", closed: "Closed" }[p.status as string];
  const ff = p.first_flood_time_forecast && p.first_flood_time_forecast !== "null" ? p.first_flood_time_forecast : null;
  const lines = [
    `<b>${esc(p.name && p.name !== "null" ? p.name : p.path ? "Path" : "Unnamed road")}</b>${p.bridge ? " (bridge)" : ""}`,
    status,
    p.status !== "dry" && p.depth_ft != null && p.depth_ft !== "null" && Number(p.depth_ft) > 0 ? `Estimated water depth ~${Number(p.depth_ft).toFixed(1)} ft` : "",
    ff && p.status === "dry" ? `Forecast to flood ~${dayClock(ff)} (NOAA forecast + terrain)` : "",
    p.stale ? "<i>Based on an offline gauge's last reading</i>" : "",
  ].filter(Boolean);
  popup.setLngLat(e.lngLat).setHTML(lines.join("<br/>")).addTo(map);
}

export const categoryLabel = (c: string) => CATEGORY_LABEL[c] ?? c;
