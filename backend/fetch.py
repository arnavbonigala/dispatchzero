import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

DATA = Path(__file__).parents[1] / "data"
SODA = "https://data.sf.gov/resource/nuek-vuh3.json"
CALTRANS = "https://cwwp2.dot.ca.gov/data/d4/cctv/cctvStatusD04.json"
WEEKS = 5
FIELDS = [
    "call_number", "unit_id", "received_dttm", "dispatch_dttm", "response_dttm",
    "on_scene_dttm", "available_dttm", "case_location", "station_area",
    "neighborhoods_analysis_boundaries", "priority", "call_type",
]


def get_json(url, params=None):
    r = requests.get(url, params=params, headers={"User-Agent": "golden-minute"}, timeout=120)
    r.raise_for_status()
    return r.json()


def window(today):
    end = today - timedelta(days=today.weekday())
    return end - timedelta(weeks=WEEKS), end


def fetch_rows(start, end):
    rows, offset = [], 0
    while True:
        q = {
            "$select": ",".join(FIELDS),
            "$where": f"unit_type='MEDIC' AND received_dttm >= '{start}' AND received_dttm < '{end}'",
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
    for c in ["received_dttm", "dispatch_dttm", "response_dttm", "on_scene_dttm", "available_dttm"]:
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
        "turnout_s": secs("response_dttm", "dispatch_dttm"),
        "drive_s": secs("on_scene_dttm", "response_dttm"),
        "busy_s": secs("available_dttm", "dispatch_dttm"),
        "historical_s": secs("on_scene_dttm", "received_dttm"),
        "historical_unit": first["unit_id"],
    })
    calls["busy_s"] = calls["busy_s"].fillna(calls["busy_s"].median())
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
