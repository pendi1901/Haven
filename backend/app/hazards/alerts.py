"""NWS alerts -> HazardZones (spec §7.4). Official headline / description /
instruction text is kept verbatim for display."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from shapely.geometry import Point, box, shape

from app.hazards.base import BaseModule, parse_dt
from app.models import HazardZone, LatLon, Metric, Source, TimeLayer
from app.sources.nws_alerts import fetch_alerts

OFFICE_NAMES = {"GSP": "NWS Greenville-Spartanburg SC", "RAH": "NWS Raleigh NC"}


def alert_severity(event: str, emergency: bool = False) -> int:
    e = (event or "").lower()
    if emergency or "emergency" in e or e.startswith("extreme wind"):
        return 4
    if "warning" in e:
        return 3
    if "watch" in e:
        return 2
    return 1


def is_warning(event: str) -> bool:
    return "warning" in (event or "").lower() or "emergency" in (event or "").lower()


def _fmt(dt, tz):
    local = dt.astimezone(ZoneInfo(tz))
    hour = local.hour % 12 or 12

    return (
        f"{local.strftime('%b')} "
        f"{local.day} at "
        f"{hour}:{local.minute:02d} "
        f"{local.strftime('%p')} "
        f"{local.strftime('%Z')}"
    )


class Alerts(BaseModule):
    name = "alerts"

    async def refresh(self, t: datetime) -> None:
        if self.replay:
            return
        try:
            self.ctx.cache.put("nws_alerts", await fetch_alerts(self.ctx.client, self.ctx.region))
        except Exception as ex:  # noqa: BLE001
            self.ctx.cache.fail("nws_alerts", f"{type(ex).__name__}: {ex}")

    def zones(self, t: datetime) -> list[HazardZone]:
        region_box = box(*self.ctx.region.bbox)
        tz = self.ctx.region.timezone
        out: list[HazardZone] = []
        if self.replay:
            for a in self.ctx.replay.alerts_at(t):
                if not a.get("geometry") or not shape(a["geometry"]).intersects(region_box):
                    continue
                office = OFFICE_NAMES.get(a.get("wfo") or self.ctx.region.replay_wfos[0], "NWS")
                if a["kind"] == "polygon":
                    # Expiry as issued; once a later extension keeps it going, say nothing.
                    valid_to = a["expire"] if a["expire"] and a["expire"] > t else None
                    headline = (f"{a['event']} issued {_fmt(a['product_issued'], tz)}"
                                + (f" until {_fmt(a['expire'], tz)}" if valid_to else ", since extended")
                                + f" by {office}")
                    emergency = a.get("is_emergency", False)
                else:
                    valid_to = None
                    headline = f"{a['event']} in effect, issued {_fmt(a['product_issue'], tz)} by {office}"
                    emergency = False
                event = a["event"] + (" (Emergency)" if emergency and "Emergency" not in a["event"] else "")
                sev = alert_severity(event, emergency)
                sim = self.ctx.region.replay_simulated
                if sim:
                    headline = "Simulated for this demo, not issued by the NWS. " + headline.split(" by ")[0] + "."
                out.append(HazardZone(
                    id=f"nws:{a['id']}", hazard="nws_alert", geometry=a["geometry"], severity=sev,
                    layer=TimeLayer.NOW if is_warning(a["event"]) or sev <= 1 else TimeLayer.FORECAST,
                    valid_from=a["issue"], valid_to=valid_to, source=Source.SIMULATED if sim else Source.NWS,
                    reason=(f"Simulated {a['event']} (demo scenario, not an NWS product)." if sim
                            else f"Official {a['event']} from {office} (archived product)."),
                    event=a["event"], headline=headline, description=a.get("description"),
                    instruction=a.get("instruction"), is_warning=is_warning(a["event"]), archived=True,
                ))
        else:
            entry = self.ctx.cache.get("nws_alerts")
            for a in entry.value or []:
                ends = parse_dt(a.get("ends") or a.get("expires"))
                if ends and ends < t:
                    continue
                params = a.get("parameters") or {}
                text = f"{a.get('headline') or ''} {a.get('description') or ''}".upper()
                emergency = ("FLASH FLOOD EMERGENCY" in text or "TORNADO EMERGENCY" in text
                             or "CATASTROPHIC" in str(params.get("flashFloodDamageThreat", "")).upper()
                             or "CATASTROPHIC" in str(params.get("tornadoDamageThreat", "")).upper())
                sev = alert_severity(a["event"], emergency)
                out.append(HazardZone(
                    id=f"nws:{a['id']}", hazard="nws_alert", geometry=a["geometry"], severity=sev,
                    layer=TimeLayer.NOW if is_warning(a["event"]) or sev <= 1 else TimeLayer.FORECAST,
                    valid_from=parse_dt(a.get("onset") or a.get("effective") or a.get("sent")) or t,
                    valid_to=ends, source=Source.NWS, reason=f"Official {a['event']} from {a.get('sender') or 'NWS'}.",
                    event=a["event"], headline=a.get("headline"), description=a.get("description"),
                    instruction=a.get("instruction"), is_warning=is_warning(a["event"]),
                ))
        out.sort(key=lambda z: (-z.severity, z.event or ""))
        return out

    def at_point(self, p: LatLon, t: datetime) -> list[HazardZone]:
        pt = Point(p.lon, p.lat)
        return [z for z in self.ctx.world(t).alerts if shape(z.geometry).covers(pt)]

    def metrics_at(self, p: LatLon, t: datetime) -> list[Metric]:
        here = self.at_point(p, t)
        warnings = [z for z in here if z.is_warning]
        watches = [z for z in here if not z.is_warning]
        if warnings:
            top = warnings[0]
            names = sorted({z.event for z in warnings})
            return [Metric(key="alerts", label="Official alerts", value=len(here), unit="active",
                           category=", ".join(names), severity=top.severity,
                           advice="Read the official alert text and instructions under What to do.", source=Source.NWS,
                           layer=TimeLayer.NOW, observed_or_valid_at=top.valid_from)]
        if watches:
            names = sorted({z.event for z in watches})
            return [Metric(key="alerts", label="Official alerts", value=len(here), unit="active",
                           category=", ".join(names), severity=1,
                           advice="Conditions are favorable for hazards. Know where you would go.",
                           source=Source.NWS, layer=TimeLayer.FORECAST, observed_or_valid_at=watches[0].valid_from)]
        return [Metric(key="alerts", label="Official alerts", value=0, unit="active", category="None",
                       severity=0, advice="No NWS watches or warnings for your location.", source=Source.NWS,
                       layer=TimeLayer.NOW, observed_or_valid_at=t)]
