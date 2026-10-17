"""Incidents around San Francisco for the map and live dispatch.

Sources (all public, no key):
- SFPD dispatched calls, real-time (DataSF gnap-fj3t, about 15 min behind) and closed history (2zdj-bwza)
- CHP live incident feed, Golden Gate dispatch center, filtered to San Francisco
- SF traffic crashes resulting in injury (DataSF ubvf-ztfx, about 7 weeks behind), as hotspots
- SFPD incident reports (DataSF wg3w-h783, about a day behind), the last 90 days counted per 150 m cell for the heatmap

Only the call type, priority, intersection and neighborhood of a police call are kept: free-text notes are dropped
and calls DataSF marks as sensitive are skipped. Officer-initiated stops and low-priority calls (C) are left out.

CLI:
  incidents.py             refresh live SFPD calls and CHP incidents once -> data/incidents.json
  incidents.py --loop      refresh every 2 minutes
  incidents.py --history   SFPD calls for the frozen week -> data/police_frozen.json (+ fixtures/police_tuesday.json)
  incidents.py --crashes   injury crashes since 2021 -> data/crash_hotspots.json (+ fixtures/crash_hotspots.json)
  incidents.py --reports   SFPD incident reports, last 90 days -> data/police_reports.json (+ fixtures/police_reports.json)
"""
import argparse
import json
import math
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).parents[1]
DATA = ROOT / "data"
FIXTURES = ROOT / "fixtures"
LIVE = DATA / "incidents.json"
FROZEN = DATA / "police_frozen.json"
HOTSPOTS = DATA / "crash_hotspots.json"
REPORTS = DATA / "police_reports.json"

POLICE_LIVE = "https://data.sf.gov/resource/gnap-fj3t.json"
POLICE_CLOSED = "https://data.sf.gov/resource/2zdj-bwza.json"
CRASHES = "https://data.sf.gov/resource/ubvf-ztfx.json"
INCIDENT_REPORTS = "https://data.sf.gov/resource/wg3w-h783.json"
CHP = "https://media.chp.ca.gov/sa_xml/sa.xml"
CHP_CENTER = "GGCC"  # Golden Gate communications center, covers SF

SF_TZ = ZoneInfo("America/Los_Angeles")
SF_BOX = (37.70, 37.84, -122.53, -122.35)
LIVE_HOURS = 3
POLL_S = 120
LIVE_MAX_AGE_S = 15 * 60  # an incidents.json older than this is ignored by dispatch
NEAR_M = 500
NEAR_MIN = 45
CHP_NEAR_M = 1500
CELL = (0.002, 0.0025)  # crash hotspot grid, about 220 m
SINCE = "2021-01-01"
MIN_CRASHES = 3
REPORT_DAYS = 90
REPORT_CELL = (0.00135, 0.0017)  # about 150 m
REPORT_MIN = 2  # cells with a single report are left out
# Report categories that describe paperwork rather than something happening at that spot
NOT_PLACES = {"Lost Property", "Case Closure", "Courtesy Report", "Recovered Vehicle", "Vehicle Impounded", "Warrant",
              "Miscellaneous Investigation", "Other", "Vehicle Misplaced"}
VIOLENT = {"Assault", "Robbery", "Homicide", "Weapons Offense", "Weapons Carrying Etc", "Sex Offense", "Rape", "Arson",
           "Human Trafficking (A), Commercial Sex Acts", "Human Trafficking, Commercial Sex Acts"}

# SFPD call types with a weapon or violence, where a crew may need police on scene first ("Fight No Weapon" is not one)
SAFETY = re.compile(r"SHOOT|SHOTS|SHOT SPOTTER|STAB|GUN|KNIFE|W/WEAPON|ADW|HOSTAGE|BARRICADE|EXPLOSI", re.I)
CRASH = re.compile(r"VEH ACCIDENT|HIT AND RUN|HIT & RUN|COLLISION", re.I)
# CHP incident type -> severity; 2 and up counts as a crash on the map
CHP_SEVERITY = [(re.compile(r"1144|FATAL|1180|MAJOR INJ|CLOSURE|SIG ALERT", re.I), 3.0),
                (re.compile(r"COLLISION|HIT AND RUN|11[78]\d", re.I), 2.0),
                (re.compile(r"HAZARD|1125|1013|1166", re.I), 1.3)]
ROUTE = re.compile(r"\b(I|US|SR|HWY)[- ]?(\d{1,3})\b", re.I)


def get(url, params=None, timeout=60):
    r = requests.get(url, params=params, headers={"User-Agent": "golden-minute"}, timeout=timeout)
    r.raise_for_status()
    return r


def write_json(path, obj):
    """Atomic write: the API and dispatch may read while this runs."""
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    tmp.replace(path)


def in_sf(lat, lon):
    return SF_BOX[0] <= lat <= SF_BOX[1] and SF_BOX[2] <= lon <= SF_BOX[3]


def now_sf():
    return datetime.now(SF_TZ).replace(tzinfo=None)


def police_rows(rows):
    """SODA rows -> incidents. Locations are DataSF's intersection points."""
    out = []
    for r in rows:
        try:
            if r.get("sensitive_call") or r.get("onview_flag") == "Y":
                continue
            lon, lat = r["intersection_point"]["coordinates"]
            lat, lon = float(lat), float(lon)
            pri = str(r.get("priority_final") or r.get("priority_original") or "")
            title = str(r.get("call_type_final_desc") or r.get("call_type_original_desc") or "").strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not title or pri not in ("A", "B") or not in_sf(lat, lon):
            continue
        out.append({"id": f"sfpd-{r.get('cad_number') or r.get('id')}", "source": "sfpd", "t": str(r["received_datetime"])[:19],
                    "lat": round(lat, 6), "lon": round(lon, 6), "title": title.title(), "priority": pri,
                    "place": str(r.get("intersection_name") or "").replace(" \\ ", " & ").title(),
                    "nbhd": r.get("analysis_neighborhood"), "safety": bool(SAFETY.search(title)), "crash": bool(CRASH.search(title))})
    return sorted(out, key=lambda x: x["t"])


def police_where(start, end):
    return (f"received_datetime between '{start:%Y-%m-%dT%H:%M:%S}' and '{end:%Y-%m-%dT%H:%M:%S}' "
            "AND sensitive_call = false AND onview_flag = 'N' AND priority_final in ('A', 'B')")


def fetch_police(url, start, end):
    rows, offset = [], 0
    while True:
        batch = get(url, {"$where": police_where(start, end), "$order": "received_datetime,cad_number", "$limit": 50000, "$offset": offset}).json()
        rows += batch
        if len(batch) < 50000:
            return police_rows(rows)
        offset += 50000


def chp_severity(kind):
    return next((f for pat, f in CHP_SEVERITY if pat.search(kind)), 1.0)


def chp_route(location):
    m = ROUTE.search(location or "")
    if not m:
        return ""
    kind = {"HWY": "SR"}.get(m.group(1).upper(), m.group(1).upper())
    return f"{kind}-{int(m.group(2))}"


def parse_chp(xml, center=CHP_CENTER):
    """CHP's XML feed -> incidents inside SF. Times are Pacific local, like 'Oct 17 2026  3:02PM'."""
    part = xml.split(f'<Dispatch ID = "{center}">', 1)
    if len(part) < 2:
        return []
    out = []
    for log in re.findall(r"<Log ID = \"(.*?)\">(.*?)</Log>", part[1].split("</Dispatch>", 1)[0], re.S):
        lid, body = log
        f = {k: (re.search(f"<{k}>\"(.*?)\"</{k}>", body, re.S) or [None, ""])[1] for k in ("LogTime", "LogType", "Location", "LocationDesc", "Area", "LATLON")}
        try:
            la, lo = f["LATLON"].split(":")
            lat, lon = int(la) / 1e6, -int(lo) / 1e6
            t = datetime.strptime(" ".join(f["LogTime"].split()), "%b %d %Y %I:%M%p")
        except ValueError:
            continue
        if not in_sf(lat, lon):
            continue
        kind = f["LogType"].split("-", 1)[-1].strip() if "-" in f["LogType"] else f["LogType"]
        out.append({"id": f"chp-{lid}", "source": "chp", "t": t.isoformat(timespec="seconds"), "lat": round(lat, 6), "lon": round(lon, 6),
                    "title": kind, "place": " ".join(f["Location"].split()), "route": chp_route(f["Location"]),
                    "safety": False, "crash": chp_severity(f["LogType"]) >= 2.0})
    return sorted(out, key=lambda x: x["t"])


def refresh_live():
    now = now_sf()
    out = {"fetched_at": now.isoformat(timespec="seconds"), "police": [], "chp": [], "errors": {}}
    try:
        out["police"] = fetch_police(POLICE_LIVE, now - timedelta(hours=LIVE_HOURS), now + timedelta(hours=1))
    except (requests.RequestException, ValueError) as e:
        out["errors"]["police"] = str(e)[:200]
    try:
        out["chp"] = parse_chp(get(CHP, timeout=30).text)
    except requests.RequestException as e:
        out["errors"]["chp"] = str(e)[:200]
    write_json(LIVE, out)
    print(f"{out['fetched_at']}: {len(out['police'])} police calls, {len(out['chp'])} CHP incidents in SF" +
          (f", errors {out['errors']}" if out["errors"] else ""))
    return out


def frozen_days():
    import pandas as pd

    t = pd.read_parquet(DATA / "calls.parquet", columns=["t", "split"])
    t = t.loc[t["split"] == "frozen", "t"]
    return datetime.fromisoformat(t.min()[:10]), datetime.fromisoformat(t.max()[:10]) + timedelta(days=1)


def fetch_history():
    start, end = frozen_days()
    rows = fetch_police(POLICE_CLOSED, start, end - timedelta(seconds=1))
    write_json(FROZEN, {"from": start.date().isoformat(), "to": (end - timedelta(days=1)).date().isoformat(), "police": rows})
    tuesday = next(d for d in (start + timedelta(days=i) for i in range(7)) if d.weekday() == 1).date().isoformat()
    write_json(FIXTURES / "police_tuesday.json", {"day": tuesday, "police": [r for r in rows if r["t"].startswith(tuesday)]})
    print(f"{len(rows)} SFPD calls {start.date()} to {(end - timedelta(days=1)).date()}; fixture day {tuesday}")


def hotspots(rows, min_n=MIN_CRASHES):
    cells = {}
    for r in rows:
        try:
            lat, lon = float(r["tb_latitude"]), float(r["tb_longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not in_sf(lat, lon):
            continue
        sev = str(r.get("collision_severity", ""))
        c = cells.setdefault((round(lat / CELL[0]), round(lon / CELL[1])), [0, 0, 0, 0.0, 0.0])
        c[0] += 1
        c[1] += sev in ("Injury (Severe)", "Fatal")
        c[2] += sev == "Fatal"
        c[3] += lat
        c[4] += lon
    out = [{"lat": round(la / n, 5), "lon": round(lo / n, 5), "n": n, "severe": s, "fatal": f} for n, s, f, la, lo in cells.values() if n >= min_n]
    return sorted(out, key=lambda h: -h["n"])


def fetch_crashes():
    rows, offset = [], 0
    while True:
        batch = get(CRASHES, {"$select": "collision_datetime,collision_severity,tb_latitude,tb_longitude",
                              "$where": f"collision_datetime >= '{SINCE}'", "$order": "collision_datetime,unique_id",
                              "$limit": 50000, "$offset": offset}, timeout=120).json()
        rows += batch
        if len(batch) < 50000:
            break
        offset += 50000
    last = max((str(r.get("collision_datetime", ""))[:10] for r in rows), default="")
    out = {"from": SINCE, "to": last, "crashes": len(rows), "cell_m": 220, "hotspots": hotspots(rows)}
    write_json(HOTSPOTS, out)
    write_json(FIXTURES / "crash_hotspots.json", out)
    print(f"{len(rows)} injury crashes {SINCE} to {last}: {len(out['hotspots'])} hotspots of {MIN_CRASHES}+")


def report_cells(rows):
    """Incident reports -> {lat, lon, n, violent, top} per cell, where top is the cell's most common category."""
    cells = {}
    for r in rows:
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
            cat = str(r.get("incident_category") or "").strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not cat or cat in NOT_PLACES or not in_sf(lat, lon):
            continue
        c = cells.setdefault((round(lat / REPORT_CELL[0]), round(lon / REPORT_CELL[1])), {"n": 0, "violent": 0, "lat": 0.0, "lon": 0.0, "cats": {}})
        c["n"] += 1
        c["violent"] += cat in VIOLENT
        c["lat"] += lat
        c["lon"] += lon
        c["cats"][cat] = c["cats"].get(cat, 0) + 1
    out = [{"lat": round(c["lat"] / c["n"], 5), "lon": round(c["lon"] / c["n"], 5), "n": c["n"], "violent": c["violent"],
            "top": max(c["cats"], key=c["cats"].get)} for c in cells.values() if c["n"] >= REPORT_MIN]
    return sorted(out, key=lambda c: -c["n"])


def fetch_reports(days=REPORT_DAYS):
    """Initial reports only (supplements repeat the same incident), with a location, from the last `days` days."""
    since = (now_sf() - timedelta(days=days)).date().isoformat()
    where = f"incident_datetime >= '{since}' AND report_type_code in ('II', 'VI') AND latitude IS NOT NULL"
    rows, offset = [], 0
    while True:
        batch = get(INCIDENT_REPORTS, {"$select": "incident_datetime,incident_category,latitude,longitude", "$where": where,
                                       "$order": "incident_datetime,row_id", "$limit": 50000, "$offset": offset}, timeout=120).json()
        rows += batch
        if len(batch) < 50000:
            break
        offset += 50000
    cells = report_cells(rows)
    last = max((str(r.get("incident_datetime", ""))[:10] for r in rows), default="")
    out = {"from": since, "to": last, "reports": sum(c["n"] for c in cells), "cell_m": 150, "cells": cells}
    write_json(REPORTS, out)
    write_json(FIXTURES / "police_reports.json", out)
    print(f"{out['reports']} SFPD incident reports {since} to {last} in {len(cells)} cells "
          f"of {REPORT_MIN}+, {sum(c['violent'] for c in cells)} of them violent")


def read_json(*paths):
    for p in paths:
        try:
            if p.exists():
                return json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
    return None


def live(max_age_s=LIVE_MAX_AGE_S):
    """The last refresh, or {} when there is none or it is too old to describe right now."""
    d = read_json(LIVE)
    try:
        if d and (now_sf() - datetime.fromisoformat(d["fetched_at"])).total_seconds() <= max_age_s:
            return d
    except (KeyError, TypeError, ValueError):
        pass
    return {}


def frozen_police(day=None):
    """Frozen-week SFPD calls (on one day if given). Without data/police_frozen.json, the Tuesday fixture for its day."""
    d = read_json(FROZEN)
    if isinstance(d, dict):
        rows = d.get("police", [])
    else:
        fx = read_json(FIXTURES / "police_tuesday.json") or {}
        rows = fx.get("police", []) if not day or fx.get("day") == day else []
    return [r for r in rows if r["t"].startswith(day)] if day else rows


def meters(lat1, lon1, lat2, lon2):
    return math.hypot((lat1 - lat2) * 111_000, (lon1 - lon2) * 111_000 * math.cos(math.radians(lat1)))


def nearby(lat, lon, t, limit=5):
    """Warnings for a dispatch at (lat, lon) at frozen-week moment t: police calls in the 45 minutes before it within
    500 m, and live CHP incidents within 1.5 km. Scene-safety calls first, then nearest."""
    when = datetime.fromisoformat(t)
    out = []
    days = {t[:10], (when - timedelta(minutes=NEAR_MIN)).date().isoformat()}
    for r in (r for d in sorted(days) for r in frozen_police(d)):
        mins = (when - datetime.fromisoformat(r["t"])).total_seconds() / 60
        d = meters(lat, lon, r["lat"], r["lon"])
        if 0 <= mins <= NEAR_MIN and d <= NEAR_M:
            out.append({**r, "distance_m": round(d), "minutes_before": round(mins)})
    for c in live().get("chp", []):
        d = meters(lat, lon, c["lat"], c["lon"])
        if d <= CHP_NEAR_M:
            out.append({**c, "distance_m": round(d), "minutes_before": None, "live": True})
    return sorted(out, key=lambda r: (not r["safety"], r["distance_m"]))[:limit]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--crashes", action="store_true")
    ap.add_argument("--reports", action="store_true")
    a = ap.parse_args()
    if a.history:
        return fetch_history()
    if a.crashes:
        return fetch_crashes()
    if a.reports:
        return fetch_reports()
    while True:
        refresh_live()
        if not a.loop:
            return
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
