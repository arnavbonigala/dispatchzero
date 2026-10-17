import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

DATA = Path(__file__).parents[1] / "data"
SODA = "https://data.sf.gov/resource/nuek-vuh3.json"
CALTRANS = "https://cwwp2.dot.ca.gov/data/d4/cctv/cctvStatusD04.json"
OFFLOAD = "https://data.sf.gov/resource/6x9q-izga.json"
OFFLOAD_MONTHS = 6
HOSPITALS = {
    "ZSFG": ("San Francisco General", True),
    "CPMC - Davies": ("CPMC Davies", True),
    "CPMC - Mission Bernal": ("CPMC Mission Bernal", True),
    "CPMC - Van Ness": ("CPMC Van Ness", True),
    "Chinese": ("Chinese Hospital", True),
    "Kaiser": ("Kaiser Permanente Medical Center Geary", True),
    "Saint Francis": ("Saint Francis Memorial", True),
    "St. Mary's": ("Saint Mary's", True),
    "UCSF - Parnassus": ("Moffett & Long", True),
    "UCSF - Mission Bay": ("Benioff Children", False),
    "VA": ("VA Medical Center", False),
}
BAND_MID_MIN = {"under 30": 15, "30-59": 45, "60-119": 90, "120-179": 150, "over 180": 200}
WEEKS = 5
FIELDS = [
    "call_number", "unit_id", "received_dttm", "entry_dttm", "dispatch_dttm", "response_dttm",
    "on_scene_dttm", "transport_dttm", "hospital_dttm", "available_dttm", "case_location", "station_area",
    "neighborhoods_analysis_boundaries", "priority", "call_type",
]


def get_json(url, params=None):
    r = requests.get(url, params=params, headers={"User-Agent": "golden-minute"}, timeout=120)
    r.raise_for_status()
    return r.json()


def window(today):
    end = today - timedelta(days=today.weekday())
    return end - timedelta(weeks=WEEKS), end


ENGINE_FIELDS = ["call_number", "unit_id", "als_unit", "received_dttm", "dispatch_dttm", "on_scene_dttm", "available_dttm"]


def fetch_rows(start, end, unit_type="MEDIC", fields=FIELDS):
    rows, offset = [], 0
    while True:
        q = {
            "$select": ",".join(fields),
            "$where": f"unit_type='{unit_type}' AND received_dttm >= '{start}' AND received_dttm < '{end}'",
            "$order": "received_dttm,unit_id",
            "$limit": 50000,
            "$offset": offset,
        }
        page = get_json(SODA, q)
        rows += page
        if len(page) < 50000:
            return rows
        offset += 50000


def build_dispatches(raw, calls):
    d = raw.dropna(subset=["dispatch_dttm", "available_dttm"])[["call_number", "unit_id", "dispatch_dttm", "available_dttm"]].copy()
    d["dispatch_dttm"] = pd.to_datetime(d["dispatch_dttm"])
    d["available_dttm"] = pd.to_datetime(d["available_dttm"])
    d = d[(d["available_dttm"] > d["dispatch_dttm"]) & (d["available_dttm"] - d["dispatch_dttm"] < pd.Timedelta(hours=6))]
    first = set(zip(calls["call_id"], calls["historical_unit"]))
    d["simulated"] = [k in first for k in zip(d["call_number"], d["unit_id"])]
    return pd.DataFrame({
        "call_id": d["call_number"],
        "unit_id": d["unit_id"],
        "start": d["dispatch_dttm"].dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "end": d["available_dttm"].dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "simulated": d["simulated"],
    }).sort_values("start").reset_index(drop=True)


def build_calls(rows, start):
    raw = pd.DataFrame(rows)
    df = raw.copy()
    df = df.dropna(subset=["on_scene_dttm", "response_dttm", "dispatch_dttm", "case_location", "station_area"])
    for c in ["received_dttm", "entry_dttm", "dispatch_dttm", "response_dttm", "on_scene_dttm", "hospital_dttm", "available_dttm"]:
        df[c] = pd.to_datetime(df[c])
    df["lon"] = df["case_location"].map(lambda p: p["coordinates"][0])
    df["lat"] = df["case_location"].map(lambda p: p["coordinates"][1])
    df = df[df["lat"].between(37.70, 37.84) & df["lon"].between(-122.53, -122.35)]

    homes = df.groupby("unit_id")["station_area"].agg(lambda s: s.mode().iloc[0])
    posts_df = df.groupby("station_area")[["lat", "lon"]].median().reset_index()

    first = df.sort_values("on_scene_dttm").groupby("call_number", as_index=False).first()
    secs = lambda a, b: (first[a] - first[b]).dt.total_seconds()
    calls = pd.DataFrame({
        "call_id": first["call_number"],
        "t": first["received_dttm"].dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "lat": first["lat"],
        "lon": first["lon"],
        "hour": first["received_dttm"].dt.hour,
        "nbhd": first["neighborhoods_analysis_boundaries"].fillna("Unknown"),
        "station_area": first["station_area"],
        "priority": first["priority"].fillna(""),
        "processing_s": secs("dispatch_dttm", "received_dttm"),
        "entry_s": secs("entry_dttm", "received_dttm"),
        "turnout_s": secs("response_dttm", "dispatch_dttm"),
        "drive_s": secs("on_scene_dttm", "response_dttm"),
        "busy_s": secs("available_dttm", "dispatch_dttm"),
        "historical_s": secs("on_scene_dttm", "received_dttm"),
        "historical_unit": first["unit_id"],
        "transported": first["hospital_dttm"].notna() if "hospital_dttm" in first else False,
        "hospital_s": secs("available_dttm", "hospital_dttm") if "hospital_dttm" in first else float("nan"),
    })
    calls["busy_s"] = calls["busy_s"].fillna(calls["busy_s"].median())
    calls["hospital_s"] = calls["hospital_s"].where(calls["hospital_s"].between(0, 6 * 3600))
    ok = (
        calls[["processing_s", "turnout_s", "drive_s"]].ge(0).all(axis=1)
        & calls["historical_s"].between(30, 3600)
        & calls["busy_s"].between(60, 6 * 3600)
    )
    calls = calls[ok].sort_values("t").reset_index(drop=True)
    calls["week"] = ((pd.to_datetime(calls["t"]) - pd.Timestamp(start)).dt.days // 7).astype(int)
    calls["split"] = calls["week"].map(lambda w: "frozen" if w == WEEKS - 1 else "holdout" if w == WEEKS - 2 else "train")

    posts = [
        {"post": i, "station_area": r.station_area, "lat": r.lat, "lon": r.lon}
        for i, r in enumerate(posts_df.itertuples())
    ]
    index = {p["station_area"]: p["post"] for p in posts}
    usual = {u: index[a] for u, a in homes.items()}
    return calls, posts, demand_homes(calls, index, sorted(usual)), usual, build_dispatches(raw, calls)


def build_engines(rows, calls, posts):
    e = pd.DataFrame(rows).dropna(subset=["dispatch_dttm", "available_dttm"])
    for c in ["received_dttm", "dispatch_dttm", "on_scene_dttm", "available_dttm"]:
        e[c] = pd.to_datetime(e[c])
    e = e[(e["available_dttm"] > e["dispatch_dttm"]) & (e["available_dttm"] - e["dispatch_dttm"] < pd.Timedelta(hours=6))]
    index = {p["station_area"]: p["post"] for p in posts}
    als = e.groupby("unit_id")["als_unit"].mean()
    engines = [{"unit_id": u, "post": index[u[1:]]} for u in sorted(als.index) if als[u] > 0.5 and u[1:] in index]
    ids = {x["unit_id"] for x in engines}
    medic = set(calls["call_id"])
    first = e[e["unit_id"].isin(ids) & e["call_number"].isin(medic)].dropna(subset=["on_scene_dttm"]).sort_values("on_scene_dttm").groupby("call_number").first()
    engine_s = (first["on_scene_dttm"] - first["received_dttm"]).dt.total_seconds()
    calls["engine_s"] = calls["call_id"].map(engine_s).where(lambda s: s.between(30, 3600))
    calls["engine_unit"] = calls["call_id"].map(first["unit_id"]).where(calls["engine_s"].notna())
    on_scene = (first["available_dttm"] - first["on_scene_dttm"]).dt.total_seconds()
    d = e[e["unit_id"].isin(ids)]
    dispatches = pd.DataFrame({
        "call_id": d["call_number"],
        "unit_id": d["unit_id"],
        "start": d["dispatch_dttm"].dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "end": d["available_dttm"].dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "simulated": d["call_number"].isin(medic),
    }).sort_values("start").reset_index(drop=True)
    return {"engines": engines, "on_scene_s": float(on_scene[on_scene > 0].median())}, dispatches


def demand_homes(calls, index, units):
    demand = calls[calls["split"] != "frozen"]["station_area"].value_counts()
    share = demand / demand.sum() * len(units)
    seats = share.astype(int)
    for area in (share - seats).sort_values(ascending=False).index[: len(units) - seats.sum()]:
        seats[area] += 1
    order = [index[a] for a, n in seats.sort_values(ascending=False).items() for _ in range(n)]
    return dict(zip(units, order))


def fetch_cameras():
    out = []
    for item in get_json(CALTRANS)["data"]:
        c = item["cctv"]
        loc, img = c["location"], c["imageData"]
        if loc["county"] != "San Francisco" or c["inService"] != "true":
            continue
        out.append({
            "camera_id": loc["locationName"].split(" -- ")[0],
            "name": loc["locationName"],
            "route": loc["route"],
            "lat": float(loc["latitude"]),
            "lon": float(loc["longitude"]),
            "stream": img["streamingVideoURL"],
            "image": img["static"]["currentImageURL"],
        })
    return out


def fetch_offload():
    rows = get_json(OFFLOAD, {"$limit": 50000, "$order": "month_start_date DESC"})
    months = sorted({r["month_start_date"] for r in rows}, reverse=True)[:OFFLOAD_MONTHS]
    out = {}
    for r in rows:
        if r["month_start_date"] not in months:
            continue
        h = out.setdefault(r["hospital_abbrev"], {"p90": [], "bands": {}})
        d, v = r["metric_definition"], float(r["value"])
        if "90th percentile" in d:
            h["p90"].append(v)
        for band, mid in BAND_MID_MIN.items():
            if band in d:
                h["bands"][mid] = h["bands"].get(mid, 0) + v
    return {
        k: {
            "offload_p90_min": round(sum(v["p90"]) / len(v["p90"]), 1),
            "offload_mean_min": round(sum(m * n for m, n in v["bands"].items()) / sum(v["bands"].values()), 1),
            "patients": int(sum(v["bands"].values())),
            "months": f"{months[-1][:7]} to {months[0][:7]}",
        }
        for k, v in out.items() if v["p90"] and v["bands"]
    }


def fetch_hospitals():
    import osmnx as ox

    g = ox.features_from_place("San Francisco, California, USA", tags={"amenity": "hospital"}).to_crs(4326)
    osm = [(str(r.get("name")), r.geometry.centroid) for _, r in g.iterrows()]
    offload = fetch_offload()
    out = []
    for abbrev, (needle, general) in HOSPITALS.items():
        match = [(n, c) for n, c in osm if needle in n]
        if not match or abbrev not in offload:
            print(f"hospital skipped: {abbrev}")
            continue
        name, c = match[0]
        out.append({"name": name, "abbrev": abbrev, "general": general, "lat": round(c.y, 6), "lon": round(c.x, 6), **offload[abbrev]})
    return out


def fetch_graph(path):
    import osmnx as ox

    g = ox.graph_from_place("San Francisco, California, USA", network_type="drive")
    g = ox.add_edge_speeds(g, fallback=40)
    g = ox.add_edge_travel_times(g)
    ox.save_graphml(g, path)
    return g


def main():
    DATA.mkdir(exist_ok=True)
    start, end = window(date.today())
    print(f"window {start} to {end} ({WEEKS} weeks, last week frozen)")

    rows = fetch_rows(start, end)
    print(f"medic dispatch rows: {len(rows)}")
    calls, posts, units, usual, dispatches = build_calls(rows, start)
    calls.to_parquet(DATA / "calls.parquet", index=False)
    dispatches.to_parquet(DATA / "dispatches.parquet", index=False)
    (DATA / "posts.json").write_text(json.dumps(posts, indent=1))
    (DATA / "units.json").write_text(json.dumps(units, indent=1))
    (DATA / "unit_areas.json").write_text(json.dumps(usual, indent=1))
    print(f"calls: {len(calls)} ({calls.groupby('split').size().to_dict()}), posts: {len(posts)}, units: {len(units)}")
    print(f"unit dispatches: {len(dispatches)} ({int(dispatches['simulated'].sum())} simulated, {int((~dispatches['simulated']).sum())} background)")
    print(f"historical median response: {calls['historical_s'].median():.0f}s")

    engine_rows = fetch_rows(start, end, "ENGINE", ENGINE_FIELDS)
    engines, engine_dispatches = build_engines(engine_rows, calls, posts)
    calls.to_parquet(DATA / "calls.parquet", index=False)
    engine_dispatches.to_parquet(DATA / "engine_dispatches.parquet", index=False)
    (DATA / "engines.json").write_text(json.dumps(engines, indent=1))
    print(f"paramedic engines: {len(engines['engines'])}, engine dispatches: {len(engine_dispatches)}, medic calls with an engine on scene: {calls['engine_s'].notna().mean():.0%}")

    hospitals = fetch_hospitals()
    (DATA / "hospitals.json").write_text(json.dumps(hospitals, indent=1))
    print(f"hospitals: {len(hospitals)} ({sum(h['general'] for h in hospitals)} general), transported calls: {int(calls['transported'].sum())}")

    cams = fetch_cameras()
    (DATA / "cameras.json").write_text(json.dumps(cams, indent=1))
    print(f"cameras: {len(cams)}")

    graph = DATA / "sf.graphml"
    if "--skip-graph" in sys.argv or graph.exists():
        print("graph: skipped" if not graph.exists() else "graph: cached")
    else:
        g = fetch_graph(graph)
        print(f"graph: {len(g.nodes)} nodes, {len(g.edges)} edges")


if __name__ == "__main__":
    main()
