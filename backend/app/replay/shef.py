"""Minimal SHEF `.E` parser for NWS River Forecast Center RVF products.

Only what Haven needs: official stage forecast series (PE=HG, type F) per
location id. Example block:

    .ER FLCN7 0927 Z DC202409261903/DH00/HGIFFZZ/DIH6
    :RIVER FORECAST  / 12Z   / 18Z   /  00Z   / 06Z
    .E1    : 0926 :                   /  18.1 /  23.9
    .E2    : 0927 :   /  29.1 /  30.5 /  29.6 /  27.5

Text between colons is a comment. Values are sequential from the start time at
the DI interval.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_HEADER = re.compile(
    r"^\.ER?\s+(?P<lid>[A-Z0-9]{3,8})\s+(?P<date>\d{4}|\d{6}|\d{8})\s+(?P<tz>Z|[A-Z]{1,2})\s+(?P<rest>.*)$"
)
_CONT = re.compile(r"^\.E(?P<n>\d+)\s*(?P<rest>.*)$")


def _strip_comments(s: str) -> str:
    out, inside = [], False
    for ch in s:
        if ch == ":":
            inside = not inside
            continue
        if not inside:
            out.append(ch)
    return "".join(out)


def _parse_value(tok: str) -> float | None:
    tok = tok.strip()
    if not tok:
        return None
    if tok.upper().startswith("M") or tok in ("-9999", "-9999.0", "+"):
        return float("nan")
    m = re.match(r"^[-+]?\d+(\.\d+)?", tok)
    return float(m.group(0)) if m else None


def _interval(code: str) -> timedelta | None:
    m = re.match(r"^DI([NHDM])(\d+)$", code)
    if not m:
        return None
    unit, n = m.group(1), int(m.group(2))
    return {"N": timedelta(minutes=n), "H": timedelta(hours=n), "D": timedelta(days=n),
            "M": timedelta(days=30 * n)}[unit]


def parse_rvf(text: str, lids: set[str] | None = None) -> list[dict]:
    """Return [{lid, issued, pe, points: [(datetime, value)]}] for stage forecasts."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    results: list[dict] = []
    cur: dict | None = None

    def flush():
        nonlocal cur
        if cur and cur["points"]:
            results.append(cur)
        cur = None

    for raw in lines:
        line = raw.strip()
        if line.startswith(".ER ") or line.startswith(".E "):
            flush()
            m = _HEADER.match(_strip_comments(line).strip())
            if not m:
                continue
            lid = m.group("lid")
            fields = [f.strip() for f in m.group("rest").split("/")]
            created = None
            hour, minute = 0, 0
            pe = None
            step = None
            values: list[str] = []
            for f in fields:
                if not f:
                    continue
                if f.startswith("DC"):
                    d = f[2:]
                    created = datetime.strptime(d[:12].ljust(12, "0"), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
                elif f.startswith("DH"):
                    hh = f[2:]
                    hour = int(hh[:2])
                    minute = int(hh[2:4]) if len(hh) >= 4 else 0
                elif f.startswith("DI"):
                    step = _interval(f)
                elif re.match(r"^[A-Z]{2}[A-Z0-9]{1,5}$", f) and pe is None and not f.startswith("D"):
                    pe = f
                else:
                    values.append(f)
            if pe is None or step is None or created is None:
                continue
            if not (pe.startswith("HG") and len(pe) >= 4 and pe[2] == "I" and pe[3] == "F"):
                continue
            if lids is not None and lid not in lids:
                continue
            date = m.group("date")
            mmdd = date[-4:]
            year = created.year
            start = datetime(year, int(mmdd[:2]), int(mmdd[2:]), hour, minute, tzinfo=timezone.utc)
            if start - created > timedelta(days=180):  # Dec product forecasting Jan
                start = start.replace(year=year - 1)
            elif created - start > timedelta(days=180):
                start = start.replace(year=year + 1)
            cur = {"lid": lid, "issued": created, "pe": pe, "start": start, "step": step, "points": [], "_n": 0}
            for v in values:
                _append(cur, v)
            continue
        m = _CONT.match(line)
        if m and cur is not None:
            body = _strip_comments(m.group("rest"))
            for tok in body.split("/"):
                _append(cur, tok)
            continue
        if line.startswith("."):
            flush()
    flush()
    for r in results:
        r.pop("_n", None)
        r.pop("start", None)
        r.pop("step", None)
    return results


def _append(cur: dict, tok: str) -> None:
    v = _parse_value(tok)
    if v is None:
        return
    t = cur["start"] + cur["step"] * cur["_n"]
    cur["_n"] += 1
    if v == v:  # skip NaN (missing) but keep the time slot
        cur["points"].append((t, v))
