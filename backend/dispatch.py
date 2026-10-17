"""Dispatch one incident live: replay the frozen week up to a moment, then ask a policy which ambulance and
paramedic engine to send.

The simulator is used unchanged. The incident is injected as a probe call at the requested moment, and a thin
wrapper around the policy records the idle units, their posts and the policy's pick when the probe comes up.
With GOOGLE_MAPS_API_KEY set (env or .env), the closest candidates are re-timed with Google's live traffic.
"""
import argparse
import json
import os
import threading
from functools import lru_cache

import numpy as np
import pandas as pd
import requests
from scipy.spatial import cKDTree

import sim

PROBE = "probe"
CANDIDATES = 6
GOOGLE_KEY = "GOOGLE_MAPS_API_KEY"
ROUTE_MATRIX = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
_lock = threading.Lock()


@lru_cache(1)
def world():
    return sim.World.load()


@lru_cache(1)
def frozen_calls():
    calls = pd.read_parquet(sim.DATA / "calls.parquet")
    calls = calls[calls["split"] == "frozen"].copy()
    calls["dispatch_epoch"] = sim.epoch(calls["t"]) + calls["processing_s"].to_numpy()
    return calls.sort_values("dispatch_epoch")


@lru_cache(1)
def place_index():
    calls = pd.read_parquet(sim.DATA / "calls.parquet", columns=["lat", "lon", "nbhd", "station_area", "priority"])
    return cKDTree(sim.xy(calls["lat"], calls["lon"])), calls.reset_index(drop=True)


@lru_cache(1)
def hospitals():
    hs = json.loads((sim.DATA / "hospitals.json").read_text())
    hs = [h for h in hs if h.get("general", True)]
    return hs, world().roads.nearest([h["lat"] for h in hs], [h["lon"] for h in hs])


def google_key():
    if os.environ.get(GOOGLE_KEY):
        return os.environ[GOOGLE_KEY]
    env = sim.ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == GOOGLE_KEY:
                return v.strip().strip("'\"")
    return None


def google_drive_s(origins, dest):
    """Live-traffic drive seconds from each (lat, lon) origin to dest, or None without a key or on failure."""
    key = google_key()
    if not key:
        return None
    point = lambda lat, lon: {"waypoint": {"location": {"latLng": {"latitude": lat, "longitude": lon}}}}
    body = {
        "origins": [point(*o) for o in origins],
        "destinations": [point(*dest)],
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
    }
    try:
        r = requests.post(ROUTE_MATRIX, json=body, timeout=8,
                          headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "originIndex,duration,condition"})
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"google routes failed: {e}")
        return None
    out = {}
    for row in r.json():
        if row.get("condition") == "ROUTE_EXISTS" and "duration" in row:
            out[row.get("originIndex", 0)] = float(row["duration"].rstrip("s"))
    return out


class Recorder:
    """Passes every decision through to the policy and keeps what it saw for the probe call."""

    def __init__(self, inner, probe):
        self.inner, self.probe, self.seen, self.engines_seen = inner, probe, None, None
        if hasattr(inner, "send_engine"):  # only when the policy has one: the simulator's default differs otherwise
            self.send_engine = self._send_engine

    def _is_probe(self, call):
        return (call["t"], call["lat"], call["lon"]) == (self.probe["t"], self.probe["lat"], self.probe["lon"])

    def post_plan(self, hour, units):
        return self.inner.post_plan(hour, units)

    def choose_unit(self, call, idle, eta):
        pick = self.inner.choose_unit(call, idle, eta)
        if self._is_probe(call):
            self.seen = {"call": call, "idle": idle, "eta": eta, "pick": pick}
        return pick

    def _send_engine(self, call, engines, eta, ambulance_s):
        pick = self.inner.send_engine(call, engines, eta, ambulance_s)
        if self._is_probe(call):
            self.engines_seen = sorted(e["unit_id"] for e in engines)
        return pick


def best_version():
    # engine mode, like the UI's replay and headline
    for path in (sim.ROOT / "runs" / "engines.jsonl", sim.FIXTURES / "engines_versions.jsonl"):
        if path.exists():
            rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            for r in reversed(rows):
                if r.get("accepted") and r["version"] != "baseline" and (sim.POLICIES / f"{r['version']}.py").exists():
                    return r["version"]
    return None


# SF's practice, which the baseline follows: an engine co-responds on 72% of Priority 3 (lights and sirens)
# calls and 4-11% of Priority 2 calls, so baseline sends the closest engine on Priority 3 only
ENGINE_PRIORITIES = {"3", "E"}


def probe_call(lat, lon, t, priority="3"):
    tree, places = place_index()
    near = places.iloc[tree.query(sim.xy([lat], [lon]))[1][0]]
    return {
        "call_id": PROBE, "t": t, "lat": lat, "lon": lon, "hour": int(t[11:13]),
        "nbhd": near["nbhd"], "station_area": near["station_area"], "priority": priority, "life_threatening": True,
        "processing_s": 0.0, "entry_s": 0.0, "turnout_s": 0.0, "drive_s": 0.0, "busy_s": 0.0, "historical_s": 0.0,
        "historical_unit": None, "transported": False, "hospital_s": float("nan"), "week": 5, "split": "frozen",
        # the simulator's default (baseline) sends the closest idle engine only when engine_s is set
        "engine_s": 0.0 if priority in ENGINE_PRIORITIES else float("nan"), "engine_unit": None,
    }


def post_ll(w, p):
    return [w.posts[p]["lat"], w.posts[p]["lon"]]


def decide(version, probe):
    w = world()
    before = frozen_calls()
    before = before[before["dispatch_epoch"] < sim.epoch([probe["t"]])[0]].drop(columns="dispatch_epoch")
    rec = Recorder(sim.load_policy(version, w), probe)
    results, _ = sim.simulate(w, before.to_dict("records") + [probe], rec, engines=True)
    row = next(r for r in reversed(results) if r["t"] == probe["t"] and r["lat"] == probe["lat"] and r["lon"] == probe["lon"])
    seen = rec.seen
    if seen is None or not seen["idle"]:
        return {"version": version, "error": "no idle ambulance at this moment"}

    call, idle, eta = seen["call"], seen["idle"], seen["eta"]
    sim_eta = {u["unit_id"]: eta(u, call) for u in idle}
    ranked = sorted(idle, key=lambda u: sim_eta[u["unit_id"]])[:CANDIDATES]
    live = google_drive_s([post_ll(w, u["post"]) for u in ranked], (probe["lat"], probe["lon"]))
    offset = w.offsets[sim.BUCKET_OF[probe["hour"]]]
    if live:
        # Google's road time replaces ours for the closest candidates; keep the calibration offset so both sources stay comparable
        timed = {u["unit_id"]: live[i] + offset for i, u in enumerate(ranked) if i in live}
        eta_live = lambda u, c: timed.get(u["unit_id"], sim_eta[u["unit_id"]])
        pick = rec.inner.choose_unit(call, idle, eta_live)
        final = {u: timed.get(u, s) for u, s in sim_eta.items()}
    else:
        pick, final = seen["pick"], sim_eta
    if pick not in final:
        pick = min(final, key=final.get)

    node = w.call_nodes[PROBE]
    unit = {u["unit_id"]: u for u in idle}
    cands = sorted(ranked, key=lambda u: final[u["unit_id"]])
    nearest = cands[0]["unit_id"]
    p = unit[pick]["post"]
    _, route = w.roads.path(w.post_nodes[p], node)
    engine = None
    if row.get("engine_unit"):
        ep = int(row["engine_post"])
        _, eroute = w.roads.path(w.post_nodes[ep], node)
        engine = {"unit_id": row["engine_unit"], "post": ep, "lat": post_ll(w, ep)[0], "lon": post_ll(w, ep)[1],
                  "eta_s": float(row["engine_eta_s"]), "route": eroute}
    amb_s = round(final[pick], 1)
    first = ("engine", engine["unit_id"], engine["eta_s"]) if engine and engine["eta_s"] < amb_s else ("ambulance", pick, amb_s)
    return {
        "version": version,
        "traffic": "google" if live else "osm",
        "n_idle": len(idle),
        "idle_ids": sorted(unit),
        "chosen": {"unit_id": pick, "post": p, "station_area": w.posts[p]["station_area"], "lat": post_ll(w, p)[0],
                   "lon": post_ll(w, p)[1], "eta_s": round(final[pick], 1), "home_post": w.homes.get(pick),
                   "moved": w.homes.get(pick) != p},
        "nearest": {"unit_id": nearest, "eta_s": round(final[nearest], 1)},
        "candidates": [{"unit_id": u["unit_id"], "post": u["post"], "lat": post_ll(w, u["post"])[0], "lon": post_ll(w, u["post"])[1],
                        "eta_s": round(final[u["unit_id"]], 1)} for u in cands],
        "route": route,
        "engine": engine,
        "engine_idle_ids": rec.engines_seen,
        "first": {"kind": first[0], "unit_id": first[1], "eta_s": first[2]},
    }


def pick_hospital(node, hour, nbhd):
    w = world()
    hs, h_nodes = hospitals()
    t = w.roads.times([node])[0]
    opts = []
    for h, hn in zip(hs, h_nodes):
        drive = float(t[hn]) * w.mult(hour, nbhd)
        offload = 60 * float(h.get("offload_mean_min") or 0)
        opts.append({"name": h["name"], "lat": h["lat"], "lon": h["lon"], "drive_s": round(drive, 1),
                     "offload_s": round(offload, 1), "total_s": round(drive + offload, 1), "node": int(hn)})
    opts.sort(key=lambda o: o["total_s"])
    best = opts[0]
    _, best["route"] = w.roads.path(node, best.pop("node"))
    for o in opts[1:]:
        o.pop("node")
    return best, opts[1:3]


def explain(base, agent, hour):
    if "error" in base or not agent or "error" in agent:
        return None
    b, a = base["chosen"], agent["chosen"]
    fb, fa = base["first"], agent["first"]
    be, ae = base["engine"], agent["engine"]
    mins = lambda s: f"{round(s / 60)} min"
    if ae and not be and fa["kind"] == "engine":
        return (f"SF usually sends no fire engine on a Priority 2 call, so baseline's first paramedic is ambulance {fb['unit_id']} "
                f"in {mins(fb['eta_s'])}. The agent sees the closest ambulance is far and adds engine {ae['unit_id']}, "
                f"{round(fb['eta_s'] - fa['eta_s'])} s sooner ({mins(fa['eta_s'])}).")
    if be and (not ae or ae["unit_id"] != be["unit_id"]) and be["unit_id"] in (agent.get("engine_idle_ids") or []):
        load = world().engine_load.get(f"{be['unit_id']}|{hour}", 0.0)
        instead = f"sends engine {ae['unit_id']} instead" if ae else "sends no engine"
        return (f"The agent keeps engine {be['unit_id']} free for fires (it averages {load:.1f} runs at {hour}:00) and {instead}. "
                f"First paramedic: {fa['kind']} {fa['unit_id']} in {mins(fa['eta_s'])}, vs {fb['kind']} {fb['unit_id']} in {mins(fb['eta_s'])} under baseline.")
    if be and ae and ae["unit_id"] != be["unit_id"] and agent.get("engine_idle_ids") is not None:
        gain = fb["eta_s"] - fa["eta_s"]
        return (f"Under the agent, engine {be['unit_id']} is already out on an earlier call (earlier dispatches went differently), "
                f"so {ae['unit_id']} goes and the first paramedic arrives {round(abs(gain))} s {'sooner' if gain > 0 else 'later'} "
                f"({mins(fa['eta_s'])} vs {mins(fb['eta_s'])} under baseline).")
    gain = fb["eta_s"] - fa["eta_s"]
    if fa["unit_id"] != fb["unit_id"] and abs(gain) >= 5:
        return (f"First paramedic {round(abs(gain))} s {'sooner' if gain > 0 else 'later'} with the agent: {fa['kind']} {fa['unit_id']} "
                f"in {mins(fa['eta_s'])}, vs {fb['kind']} {fb['unit_id']} in {mins(fb['eta_s'])} under baseline.")
    gain = b["eta_s"] - a["eta_s"]
    if b["unit_id"] == a["unit_id"] or abs(gain) < 5:
        first = f"engine {fa['unit_id']} reaches the patient first in {mins(fa['eta_s'])}, " if fa["kind"] == "engine" else ""
        return f"Both send the same units: {first}ambulance {a['unit_id']} in {mins(a['eta_s'])}."
    where = f"post {a['post']} (station area {a['station_area']})"
    if a["moved"]:
        why = f"the agent had moved {a['unit_id']} to {where} ahead of time"
    elif a["unit_id"] not in base["idle_ids"]:
        why = f"{a['unit_id']} at {where} is still free, because earlier calls played out differently under the agent's positioning; under baseline it is already out on a call"
    else:
        why = f"the agent sends {a['unit_id']} from {where}"
    lead = f"{round(abs(gain))} s faster" if gain > 0 else f"{round(-gain)} s slower"
    return f"Agent arrives {lead}: {why}. Baseline's closest idle unit is {b['unit_id']} at post {b['post']}."


def dispatch(lat, lon, t, version=None, priority="3"):
    version = version or best_version()
    probe = probe_call(float(lat), float(lon), t, priority)
    w = world()
    with _lock:  # the probe's road node lives on the shared world for the length of the replay
        w.call_nodes[PROBE] = int(w.roads.nearest([probe["lat"]], [probe["lon"]])[0])
        try:
            base = decide("baseline", probe)
            agent = decide(version, probe) if version else None
            node = w.call_nodes[PROBE]
        finally:
            w.call_nodes.pop(PROBE, None)
    hospital, alternatives = pick_hospital(node, probe["hour"], probe["nbhd"])
    return {
        "incident": {k: probe[k] for k in ("t", "lat", "lon", "hour", "nbhd", "station_area", "priority")},
        "baseline": base,
        "agent": agent,
        "reason": explain(base, agent, probe["hour"]),
        "hospital": hospital,
        "hospital_alternatives": alternatives,
        "traffic": "google" if google_key() else "osm",
    }


def main():
    ap = argparse.ArgumentParser(description="Dispatch one incident in the frozen week")
    ap.add_argument("lat", type=float)
    ap.add_argument("lon", type=float)
    ap.add_argument("t", help="frozen-week moment, e.g. 2026-10-06T17:40:00")
    ap.add_argument("--policy")
    ap.add_argument("--priority", default="3", choices=["2", "3"])
    a = ap.parse_args()
    out = dispatch(a.lat, a.lon, a.t, a.policy, a.priority)
    for side in ("baseline", "agent"):
        if out[side]:
            out[side]["route"] = f"{len(out[side].get('route', []))} points"
    out["hospital"]["route"] = f"{len(out['hospital']['route'])} points"
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
