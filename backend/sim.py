import argparse
import bisect
import importlib.util
import json
import statistics
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

ROOT = Path(__file__).parents[1]
DATA = ROOT / "data"
FIXTURES = ROOT / "fixtures"
POLICIES = Path(__file__).parent / "policies"

BUCKETS = {"night": range(0, 7), "morning_rush": range(7, 10), "midday": range(10, 16), "evening_rush": range(16, 19), "evening": range(19, 24)}
BUCKET_OF = {h: b for b, hs in BUCKETS.items() for h in hs}
SHIFT_GAP_S = 6 * 3600
SHIFT_LEAD_S = 3600
MIN_ON_SCENE_S = 300
FALLBACK_MPS = 8.0
DETOUR = 1.3
PUBLIC = ("call_id", "t", "lat", "lon", "hour", "nbhd", "station_area", "priority")


def epoch(ts):
    return pd.to_datetime(pd.Series(ts)).astype("datetime64[s]").astype("int64").to_numpy()


def xy(lat, lon):
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    return np.column_stack([lon * 111320 * np.cos(np.radians(37.77)), lat * 110540])


class Roads:
    def __init__(self, path=DATA / "sf.graphml"):
        import osmnx as ox

        g = ox.load_graphml(path)
        nodes = list(g.nodes)
        idx = {n: i for i, n in enumerate(nodes)}
        self.n = len(nodes)
        self.node_xy = xy([g.nodes[n]["y"] for n in nodes], [g.nodes[n]["x"] for n in nodes])
        self.tree = cKDTree(self.node_xy)
        best = {}
        for u, v, d in g.edges(data=True):
            key = (idx[u], idx[v])
            t = float(d["travel_time"])
            if key not in best or t < best[key][0]:
                best[key] = (t, d.get("highway"), d.get("ref"))
        self.edges = list(best)
        self.edge_u = np.array([e[0] for e in self.edges])
        self.edge_v = np.array([e[1] for e in self.edges])
        self.edge_t = np.array([best[e][0] for e in self.edges])
        self.edge_highway = [best[e][1] for e in self.edges]
        self.edge_ref = [best[e][2] for e in self.edges]
        self._cache = {}

    def nearest(self, lat, lon):
        return self.tree.query(xy(lat, lon))[1]

    def times_from(self, sources, slowdowns=None):
        key = (tuple(sources), tuple(sorted((slowdowns or {}).items())))
        if key not in self._cache:
            w = self.edge_t.copy()
            for e, f in (slowdowns or {}).items():
                w[e] *= f
            m = csr_matrix((w, (self.edge_u, self.edge_v)), shape=(self.n, self.n))
            t = dijkstra(m, directed=True, indices=list(sources))
            straight = np.linalg.norm(self.node_xy[None, :, :] - self.node_xy[list(sources)][:, None, :], axis=2)
            self._cache[key] = np.where(np.isfinite(t), t, straight * DETOUR / FALLBACK_MPS)
        return self._cache[key]


def shift_windows(dispatches):
    out = {}
    for unit, g in dispatches.groupby("unit_id"):
        s, e = epoch(g["start"]), epoch(g["end"])
        order = np.argsort(s)
        wins = []
        for a, b in zip(s[order], e[order]):
            if wins and a - wins[-1][1] <= SHIFT_GAP_S:
                wins[-1][1] = max(wins[-1][1], b)
            else:
                wins.append([a - SHIFT_LEAD_S, b])
        out[unit] = ([w[0] for w in wins], [w[1] for w in wins])
    return out


def on_duty(windows, unit, t):
    starts, ends = windows.get(unit, ([], []))
    i = bisect.bisect_right(starts, t) - 1
    return i >= 0 and t <= ends[i]


def load_policy(policy):
    if not isinstance(policy, str):
        return policy
    path = Path(policy) if policy.endswith(".py") else POLICIES / f"{policy}.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class World:
    def __init__(self, travel, post_nodes, call_nodes, homes, windows, background, factors):
        self.travel = travel
        self.post_nodes = post_nodes
        self.call_nodes = call_nodes
        self.homes = homes
        self.units = sorted(homes)
        self.windows = windows
        self.background = background
        self.factors = factors

    @classmethod
    def load(cls, roads=None, slowdowns=None, factors=None):
        roads = roads or Roads()
        posts = json.loads((DATA / "posts.json").read_text())
        homes = json.loads((DATA / "units.json").read_text())
        calls = pd.read_parquet(DATA / "calls.parquet")
        dispatches = pd.read_parquet(DATA / "dispatches.parquet")
        post_nodes = roads.nearest([p["lat"] for p in posts], [p["lon"] for p in posts])
        call_nodes = dict(zip(calls["call_id"], roads.nearest(calls["lat"], calls["lon"])))
        bg = dispatches[~dispatches["simulated"]]
        background = sorted(zip(epoch(bg["start"]), epoch(bg["end"]), bg["unit_id"]))
        world = cls(roads.times_from(post_nodes, slowdowns), post_nodes, call_nodes, homes, shift_windows(dispatches), background, factors or load_factors())
        world.roads = roads
        return world

    def eta(self, unit, call):
        return float(self.travel[unit["post"], self.call_nodes[call["call_id"]]]) * self.factors[BUCKET_OF[call["hour"]]]

    def move_time(self, a, b, hour):
        return float(self.travel[a, self.post_nodes[b]]) * self.factors[BUCKET_OF[hour]]


def load_factors():
    path = DATA / "calibration.json"
    if path.exists():
        return json.loads(path.read_text())["factors"]
    return {b: 1.0 for b in BUCKETS}


def load_calls(split=None):
    calls = pd.read_parquet(DATA / "calls.parquet")
    if split:
        calls = calls[calls["split"] == split]
    return calls.to_dict("records")


def simulate(world, calls, policy):
    policy = load_policy(policy)
    busy = {u: 0 for u in world.units}
    post = dict(world.homes)
    plans = {}
    bg = world.background
    bi = 0
    hour_key = None
    results = []
    stats = {"queued": 0, "invalid_choice": 0, "relocations": 0}
    times = epoch([c["t"] for c in calls])
    for c, t0 in zip(calls, times):
        t = t0 + c["processing_s"]
        while bi < len(bg) and bg[bi][0] <= t:
            s, e, u = bg[bi]
            if u in busy:
                busy[u] = max(busy[u], e)
            bi += 1
        key = (t // 3600, c["hour"])
        if key != hour_key:
            hour_key = key
            if c["hour"] not in plans:
                plans[c["hour"]] = policy.post_plan(c["hour"], list(world.units))
            for u, p in plans[c["hour"]].items():
                if u in post and p != post[u] and 0 <= p < len(world.post_nodes):
                    busy[u] = max(busy[u], t) + world.move_time(post[u], p, c["hour"])
                    post[u] = p
                    stats["relocations"] += 1
        duty = [u for u in world.units if on_duty(world.windows, u, t)] or world.units
        idle = [{"unit_id": u, "post": post[u]} for u in duty if busy[u] <= t]
        wait = 0.0
        if idle:
            chosen = policy.choose_unit({k: c[k] for k in PUBLIC if k in c}, idle, world.eta)
            if chosen not in {u["unit_id"] for u in idle}:
                stats["invalid_choice"] += 1
                chosen = min(idle, key=lambda u: world.eta(u, c))["unit_id"]
        else:
            chosen = min(duty, key=lambda u: busy[u])
            wait = busy[chosen] - t
            stats["queued"] += 1
        unit = {"unit_id": chosen, "post": post[chosen]}
        drive = world.eta(unit, c)
        response = c["processing_s"] + wait + c["turnout_s"] + drive
        on_scene_and_after = max(c["busy_s"] - c["turnout_s"] - c["drive_s"], MIN_ON_SCENE_S)
        busy[chosen] = t + wait + c["turnout_s"] + drive + on_scene_and_after
        results.append({
            "call_id": c["call_id"],
            "t": c["t"],
            "lat": c["lat"],
            "lon": c["lon"],
            "unit": chosen,
            "from_post": int(post[chosen]),
            "eta_s": round(drive, 1),
            "response_s": round(response, 1),
            "historical_s": c["historical_s"],
            "hour": int(c["hour"]),
            "nbhd": c["nbhd"],
            "wait_s": round(wait, 1),
            "historical_drive_s": c["drive_s"],
        })
    return results, stats


def summarize(results, version, split):
    r = sorted(x["response_s"] for x in results)
    return {
        "version": version,
        "split": split,
        "median_s": statistics.median(r),
        "p90_s": r[int(0.9 * (len(r) - 1))],
        "pct_under_8min": sum(x <= 480 for x in r) / len(r),
        "n": len(r),
    }


def replay(calls, policy="baseline", slowdowns=None, version=None, split="train", world=None):
    world = world or World.load(slowdowns=slowdowns)
    results, _ = simulate(world, calls, policy)
    name = version or (policy if isinstance(policy, str) else getattr(policy, "__name__", "policy"))
    return results, summarize(results, name, split)


def calibrate(world, iters=8):
    calls = load_calls("train")
    factors = {b: 1.0 for b in BUCKETS}
    for i in range(iters):
        world.factors = factors
        results, _ = simulate(world, calls, "baseline")
        ratios = {}
        for b, hs in BUCKETS.items():
            rows = [r for r in results if r["hour"] in hs]
            ratios[b] = statistics.median(r["historical_drive_s"] for r in rows) / statistics.median(r["eta_s"] for r in rows)
        factors = {b: factors[b] * ratios[b] for b in BUCKETS}
        print(f"iter {i}: " + ", ".join(f"{b}={factors[b]:.2f}" for b in BUCKETS))
        if all(abs(r - 1) < 0.005 for r in ratios.values()):
            break
    world.factors = factors
    (DATA / "calibration.json").write_text(json.dumps({"factors": factors}, indent=1))
    return factors


def calibration_report(world):
    out = {}
    for split in ["train", "frozen"]:
        calls = load_calls(split)
        results, stats = simulate(world, calls, "baseline")
        sim = statistics.median(r["response_s"] for r in results)
        hist = statistics.median(r["historical_s"] for r in results)
        out[split] = {"sim_median_s": sim, "actual_median_s": hist, "error_pct": round(100 * (sim - hist) / hist, 2), **stats}
        by_bucket = {}
        for b, hs in BUCKETS.items():
            rows = [r for r in results if r["hour"] in hs]
            by_bucket[b] = (round(statistics.median(r["response_s"] for r in rows)), round(statistics.median(r["historical_s"] for r in rows)))
        out[split]["by_bucket_sim_vs_actual"] = by_bucket
    return out


def self_check():
    travel = np.array([[0.0, 100.0, 400.0, 900.0], [400.0, 0.0, 100.0, 900.0]])
    world = World(travel, np.array([0, 2]), {"c1": 3, "c2": 2, "c3": 2, "c4": 1}, {"A": 0, "B": 1}, {}, [], {b: 1.0 for b in BUCKETS})
    base = {"processing_s": 60.0, "turnout_s": 10.0, "drive_s": 100.0, "busy_s": 1800.0, "historical_s": 200.0, "hour": 12, "nbhd": "x", "lat": 0.0, "lon": 0.0}
    calls = [
        {**base, "call_id": "c2", "t": "2026-01-01T12:00:00"},
        {**base, "call_id": "c3", "t": "2026-01-01T12:01:00"},
        {**base, "call_id": "c4", "t": "2026-01-01T12:02:00"},
    ]
    policy = load_policy("baseline")
    policy.HOME = {"A": 0, "B": 1}
    r, stats = simulate(world, calls, policy)
    assert r[0]["unit"] == "B" and r[0]["eta_s"] == 100.0, r[0]
    assert r[1]["unit"] == "A" and r[1]["eta_s"] == 400.0, r[1]
    assert r[2]["wait_s"] > 0 and stats["queued"] == 1, r[2]
    assert r[0]["response_s"] == 60 + 10 + 100

    world.factors = {b: 2.0 for b in BUCKETS}
    r2, _ = simulate(world, calls[:1], policy)
    assert r2[0]["eta_s"] == 200.0

    world.factors = {b: 1.0 for b in BUCKETS}
    world.background = [(int(epoch(["2026-01-01T11:59:00"])[0]), int(epoch(["2026-01-01T13:00:00"])[0]), "B")]
    r3, _ = simulate(world, calls[:1], policy)
    assert r3[0]["unit"] == "A", r3[0]

    world.background = []
    world.windows = {"A": ([0], [10]), "B": ([0], [2**40])}
    r4, _ = simulate(world, calls[:2], policy)
    assert r4[0]["unit"] == "B" and r4[1]["unit"] == "B" and r4[1]["wait_s"] > 0, r4

    class Mover:
        @staticmethod
        def post_plan(hour, units):
            return {"A": 1, "B": 1}

        choose_unit = staticmethod(policy.choose_unit)

    world.windows = {}
    r5, s5 = simulate(world, calls[:1], Mover)
    assert s5["relocations"] == 1 and r5[0]["unit"] == "B", (r5, s5)

    class Bad:
        post_plan = staticmethod(policy.post_plan)

        @staticmethod
        def choose_unit(call, idle, eta):
            return "nope"

    _, s6 = simulate(world, calls[:1], Bad)
    assert s6["invalid_choice"] == 1

    roads = Roads()
    src = roads.nearest([37.7793], [-122.4193])
    plain = roads.times_from(src)
    slow = roads.times_from(src, {e: 3.0 for e in range(len(roads.edges))})
    far = roads.nearest([37.7599], [-122.4148])[0]
    assert np.isclose(slow[0, far], 3 * plain[0, far]) and 120 < plain[0, far] < 900, plain[0, far]
    print("self-check ok")


def write_fixtures(world):
    FIXTURES.mkdir(exist_ok=True)
    calls = [c for c in load_calls("frozen") if pd.Timestamp(c["t"]).dayofweek == 1]
    results, summary = replay(calls, "baseline", version="baseline", split="frozen", world=world)
    (FIXTURES / "results.json").write_text(json.dumps(results, indent=1))
    (FIXTURES / "summary.json").write_text(json.dumps(summary, indent=1))
    print(f"fixtures: {len(results)} results, median {summary['median_s']:.0f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", default="baseline")
    ap.add_argument("--split", default="train", choices=["train", "frozen"])
    ap.add_argument("--out")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--fixtures", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    world = World.load()
    if a.calibrate:
        calibrate(world)
        print(json.dumps(calibration_report(world), indent=1))
        return
    if a.fixtures:
        return write_fixtures(world)
    results, stats = simulate(world, load_calls(a.split), a.policy)
    summary = summarize(results, Path(a.policy).stem, a.split)
    summary["actual_median_s"] = statistics.median(r["historical_s"] for r in results)
    summary["stats"] = stats
    if a.out:
        Path(a.out).write_text(json.dumps({"summary": summary, "results": results}))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
