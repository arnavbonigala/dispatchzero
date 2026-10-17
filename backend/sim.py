import argparse
import ast
import bisect
import importlib.util
import json
import random
import statistics
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree
from scipy.stats import spearmanr

ROOT = Path(__file__).parents[1]
DATA = ROOT / "data"
FIXTURES = ROOT / "fixtures"
POLICIES = Path(__file__).parent / "policies"

BUCKETS = {"night": range(0, 7), "morning_rush": range(7, 10), "midday": range(10, 16), "evening_rush": range(16, 19), "evening": range(19, 24)}
BUCKET_OF = {h: b for b, hs in BUCKETS.items() for h in hs}
SPLITS = {"train": ["train"], "holdout": ["holdout"], "frozen": ["frozen"], "fit": ["train", "holdout"]}
SHIFT_GAP_S = 6 * 3600
SHIFT_LEAD_S = 3600
MIN_ON_SCENE_S = 300
FALLBACK_MPS = 8.0
DETOUR = 1.3
FREEWAY = {"motorway", "motorway_link"}
PUBLIC = ("t", "lat", "lon", "hour", "nbhd", "station_area", "priority")
ALLOWED_IMPORTS = {"math", "statistics", "collections", "itertools", "functools", "heapq", "bisect", "random"}
BANNED_NAMES = {"open", "eval", "exec", "compile", "globals", "locals", "vars", "getattr", "setattr", "delattr", "__import__", "input", "breakpoint", "memoryview", "type", "object", "super"}


class PolicyRejected(Exception):
    pass


def epoch(ts):
    return pd.to_datetime(pd.Series(ts)).astype("datetime64[s]").astype("int64").to_numpy()


def xy(lat, lon):
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    return np.column_stack([lon * 111320 * np.cos(np.radians(37.77)), lat * 110540])


def check_source(src):
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            bad = [a.name for a in node.names if a.name.split(".")[0] not in ALLOWED_IMPORTS]
        elif isinstance(node, ast.ImportFrom):
            bad = [node.module] if (node.module or "").split(".")[0] not in ALLOWED_IMPORTS else []
        elif isinstance(node, ast.Attribute):
            bad = [node.attr] if node.attr.startswith("_") else []
        elif isinstance(node, ast.Name):
            bad = [node.id] if node.id in BANNED_NAMES or node.id.startswith("__") else []
        else:
            bad = []
        if bad:
            raise PolicyRejected(f"policy uses disallowed {bad[0]!r} (line {getattr(node, 'lineno', '?')})")


class Roads:
    def __init__(self, path=DATA / "sf.graphml"):
        import osmnx as ox

        g = ox.load_graphml(path)
        nodes = list(g.nodes)
        idx = {n: i for i, n in enumerate(nodes)}
        self.n = len(nodes)
        self.node_xy = xy([g.nodes[n]["y"] for n in nodes], [g.nodes[n]["x"] for n in nodes])
        best = {}
        street = np.zeros(self.n, bool)
        for u, v, d in g.edges(data=True):
            hw = d.get("highway")
            hw = set(hw) if isinstance(hw, list) else {hw}
            if not hw <= FREEWAY:
                street[idx[u]] = street[idx[v]] = True
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
        m = csr_matrix((self.edge_t, (self.edge_u, self.edge_v)), shape=(self.n, self.n))
        _, label = connected_components(m, directed=True, connection="strong")
        core = label == np.bincount(label).argmax()
        self.snap_nodes = np.flatnonzero(core & street)
        self.tree = cKDTree(self.node_xy[self.snap_nodes])
        self._cache = {}

    def nearest(self, lat, lon):
        return self.snap_nodes[self.tree.query(xy(lat, lon))[1]]

    def _matrix(self, slowdowns, reverse=False):
        w = self.edge_t.copy()
        for e, f in (slowdowns or {}).items():
            w[e] *= f
        u, v = (self.edge_v, self.edge_u) if reverse else (self.edge_u, self.edge_v)
        return csr_matrix((w, (u, v)), shape=(self.n, self.n))

    def times(self, sources, slowdowns=None, reverse=False):
        key = (tuple(int(s) for s in sources), tuple(sorted((slowdowns or {}).items())), reverse)
        if key not in self._cache:
            t = dijkstra(self._matrix(slowdowns, reverse), directed=True, indices=list(sources))
            straight = np.linalg.norm(self.node_xy[None, :, :] - self.node_xy[list(sources)][:, None, :], axis=2)
            out = np.where(np.isfinite(t), t, straight * DETOUR / FALLBACK_MPS)
            out.setflags(write=False)
            self._cache[key] = out
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


def load_policy(policy, world):
    if not isinstance(policy, (str, Path)):
        return policy
    policy = str(policy)
    path = Path(policy) if policy.endswith(".py") else POLICIES / f"{policy}.py"
    check_source(path.read_text())
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    mod.HOME = dict(world.homes)
    mod.POSTS = [dict(p) for p in world.posts]
    mod.DEMAND = {b: dict(d) for b, d in world.demand().items()}
    mod.POST_DRIVE = [[round(float(world.travel[a, n]), 1) for n in world.post_nodes] for a in range(len(world.post_nodes))]
    mod.BUCKET_OF = dict(BUCKET_OF)
    spec.loader.exec_module(mod)
    return mod


class World:
    def __init__(self, travel, back, post_nodes, call_nodes, homes, windows, background, offsets, posts=(), rel=None):
        self.travel = travel
        self.back = back
        self.post_nodes = post_nodes
        self.call_nodes = call_nodes
        self.homes = homes
        self.units = sorted(homes)
        self.windows = windows
        self.background = background
        self.offsets = offsets
        self.posts = list(posts)
        self.rel = rel or {}

    @classmethod
    def load(cls, roads=None, slowdowns=None, offsets=None):
        roads = roads or Roads()
        posts = json.loads((DATA / "posts.json").read_text())
        homes = json.loads((DATA / "units.json").read_text())
        calls = pd.read_parquet(DATA / "calls.parquet")
        dispatches = pd.read_parquet(DATA / "dispatches.parquet")
        post_nodes = roads.nearest([p["lat"] for p in posts], [p["lon"] for p in posts])
        call_nodes = dict(zip(calls["call_id"], roads.nearest(calls["lat"], calls["lon"])))
        bg = dispatches[~dispatches["simulated"]]
        background = sorted(zip(epoch(bg["start"]), epoch(bg["end"]), bg["unit_id"]))
        world = cls(
            roads.times(post_nodes, slowdowns),
            roads.times(post_nodes, slowdowns, reverse=True),
            post_nodes, call_nodes, homes, shift_windows(dispatches), background,
            offsets or load_offsets(), posts, load_rel(),
        )
        world.roads = roads
        world.usual = json.loads((DATA / "unit_areas.json").read_text())
        return world

    def demand(self):
        if not hasattr(self, "_demand"):
            out = {b: {} for b in BUCKETS}
            for c in load_calls("train") if self.posts else []:
                p = int(np.argmin(self.travel[:, self.call_nodes[c["call_id"]]]))
                d = out[BUCKET_OF[c["hour"]]]
                d[p] = d.get(p, 0) + 1
            self._demand = out
        return self._demand

    def mult(self, hour, nbhd=None):
        return self.rel.get(f"{nbhd}|{BUCKET_OF[hour]}", 1.0)

    def drive(self, post, node, hour, nbhd=None):
        return float(self.travel[post, node]) * self.mult(hour, nbhd) + self.offsets[BUCKET_OF[hour]]

    def return_drive(self, post, node, hour, nbhd=None):
        return float(self.back[post, node]) * self.mult(hour, nbhd)

    def move_time(self, a, b, hour):
        return float(self.travel[a, self.post_nodes[b]])


def make_eta(world, nodes):
    travel = world.travel
    offsets = dict(world.offsets)
    rel = dict(world.rel)
    n_posts = len(world.post_nodes)

    def eta(unit, call):
        post = int(unit["post"])
        if not 0 <= post < n_posts:
            raise ValueError(f"no post {post}")
        b = BUCKET_OF[int(call["hour"])]
        return float(travel[post, nodes[call["call_id"]]]) * rel.get(f"{call.get('nbhd')}|{b}", 1.0) + offsets[b]

    return eta


def valid_post(p, n):
    return isinstance(p, (int, np.integer)) and not isinstance(p, bool) and 0 <= p < n


def load_offsets():
    path = DATA / "calibration.json"
    if path.exists():
        return json.loads(path.read_text()).get("offsets", {b: 0.0 for b in BUCKETS})
    return {b: 0.0 for b in BUCKETS}


def load_rel():
    path = DATA / "speeds.json"
    if path.exists():
        d = json.loads(path.read_text())
        if d.get("adopted"):
            return d["rel"]
    return {}


def load_calls(split=None):
    calls = pd.read_parquet(DATA / "calls.parquet")
    if split:
        calls = calls[calls["split"].isin(SPLITS[split])]
    return calls.to_dict("records")


def simulate(world, calls, policy, trace=False):
    policy = load_policy(policy, world)
    n_posts = len(world.post_nodes)
    busy = {u: 0 for u in world.units}
    post = dict(world.homes)
    bg = world.background
    bi = 0
    current_hour = None
    results = []
    stats = {"queued": 0, "invalid_choice": 0, "policy_errors": 0, "relocations": 0}
    tokens = {}
    eta = make_eta(world, tokens)
    prev = dict(post)
    arrive = {u: 0 for u in world.units}
    dispatch_t = epoch([c["t"] for c in calls]) + np.array([c["processing_s"] for c in calls])
    for i in np.argsort(dispatch_t, kind="stable"):
        c, t = calls[i], int(dispatch_t[i])
        at = lambda u: post[u] if t >= arrive[u] else prev[u]
        while bi < len(bg) and bg[bi][0] <= t:
            s, e, u = bg[bi]
            if u in busy:
                busy[u] = max(busy[u], e)
            bi += 1
        if t // 3600 != current_hour:
            current_hour = t // 3600
            hour = int(current_hour % 24)
            boundary = current_hour * 3600
            shift = [u for u in world.units if on_duty(world.windows, u, boundary)]
            try:
                plan = policy.post_plan(hour, shift)
                plan = {u: int(p) for u, p in dict(plan).items() if u in post and valid_post(p, n_posts)}
            except Exception:
                stats["policy_errors"] += 1
                plan = {}
            for u, p in plan.items():
                if p != post[u]:
                    start = max(busy[u], boundary)
                    prev[u] = post[u]
                    arrive[u] = start + world.move_time(post[u], p, hour)
                    post[u] = p
                    stats["relocations"] += 1
        token = f"call{len(tokens)}"
        tokens[token] = world.call_nodes[c["call_id"]]
        public = {"call_id": token, **{k: c[k] for k in PUBLIC if k in c}}
        node = tokens[token]
        duty = [u for u in world.units if on_duty(world.windows, u, t)] or world.units
        idle_ids = [u for u in duty if busy[u] <= t]
        if idle_ids:
            try:
                chosen = policy.choose_unit(public, [{"unit_id": u, "post": at(u)} for u in idle_ids], eta)
            except Exception:
                stats["policy_errors"] += 1
                chosen = None
            if not isinstance(chosen, str) or chosen not in idle_ids:
                stats["invalid_choice"] += 1
                chosen = min(idle_ids, key=lambda u: world.drive(at(u), node, c["hour"], c["nbhd"]))
        else:
            chosen = min(duty, key=lambda u: busy[u])
            stats["queued"] += 1
        start = max(busy[chosen], t)
        wait = start - t
        origin = at(chosen) if wait == 0 else post[chosen]
        arrive[chosen] = 0
        drive = world.drive(origin, node, c["hour"], c["nbhd"])
        if trace:
            best_post = int(np.argmin(world.travel[:, node]))
            state = lambda u: "idle" if u in idle_ids else "busy" if u in duty else "off_duty"
            ctx = {
                "processing_s": c["processing_s"],
                "turnout_s": c["turnout_s"],
                "n_on_duty": len(duty),
                "n_idle": len(idle_ids),
                "closest_post": best_post,
                "closest_post_area": world.posts[best_post]["station_area"] if world.posts else None,
                "closest_post_drive_s": round(world.drive(best_post, node, c["hour"], c["nbhd"]), 1),
                "units_at_closest_post": {u: state(u) for u in world.units if at(u) == best_post},
                "from_post_area": world.posts[origin]["station_area"] if world.posts else None,
            }
        response = c["processing_s"] + wait + c["turnout_s"] + drive
        after = max(c["busy_s"] - c["turnout_s"] - c["drive_s"], MIN_ON_SCENE_S)
        busy[chosen] = start + c["turnout_s"] + drive + after + world.return_drive(post[chosen], node, c["hour"], c["nbhd"])
        results.append({
            "call_id": c["call_id"],
            "t": c["t"],
            "lat": c["lat"],
            "lon": c["lon"],
            "unit": chosen,
            "from_post": int(origin),
            "eta_s": round(drive, 1),
            "response_s": round(response, 1),
            "historical_s": c["historical_s"],
            "hour": int(c["hour"]),
            "nbhd": c["nbhd"],
            "wait_s": round(wait, 1),
            "historical_drive_s": c["drive_s"],
            **(ctx if trace else {}),
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


def paired_ci(a, b, n=1000, seed=0):
    by_id = {r["call_id"]: r["response_s"] for r in b}
    pairs = [(r["response_s"], by_id[r["call_id"]]) for r in a if r["call_id"] in by_id]
    rng = random.Random(seed)
    diffs = []
    for _ in range(n):
        s = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        diffs.append(statistics.median(x for x, _ in s) - statistics.median(y for _, y in s))
    diffs.sort()
    point = statistics.median(x for x, _ in pairs) - statistics.median(y for _, y in pairs)
    return {"median_diff_s": point, "ci95": [diffs[int(0.025 * n)], diffs[int(0.975 * n) - 1]], "n": len(pairs)}


def worst_calls(world, calls, policy, n=50):
    results, stats = simulate(world, calls, policy, trace=True)
    for r in results:
        r["controllable_s"] = round(r["wait_s"] + r["eta_s"], 1)
        r["weekday"] = pd.Timestamp(r["t"]).day_name()
        r["bucket"] = BUCKET_OF[r["hour"]]
        r["avoidable_s"] = round(max(r["controllable_s"] - r["closest_post_drive_s"], 0), 1)
    worst = sorted(results, key=lambda r: -r["controllable_s"])[:n]
    keep = ["t", "weekday", "hour", "bucket", "nbhd", "station_area", "priority", "controllable_s", "wait_s", "eta_s", "avoidable_s", "unit", "from_post", "from_post_area",
            "closest_post", "closest_post_area", "closest_post_drive_s", "units_at_closest_post", "n_idle", "n_on_duty"]
    calls_by_id = {c["call_id"]: c for c in calls}
    rows = [{k: (calls_by_id[r["call_id"]].get(k) if k == "station_area" else r.get(k)) for k in keep} for r in worst]
    count = lambda key: dict(pd.Series([r[key] for r in worst]).value_counts().head(8))
    avoidable = pd.DataFrame(results).groupby(["nbhd", "bucket"])["avoidable_s"].sum().sort_values(ascending=False).head(10)
    return {
        "summary": summarize(results, Path(str(policy)).stem, "train"),
        "stats": stats,
        "worst": rows,
        "worst_by_nbhd": {k: int(v) for k, v in count("nbhd").items()},
        "worst_by_bucket": {k: int(v) for k, v in count("bucket").items()},
        "queued_in_worst": sum(r["wait_s"] > 0 for r in worst),
        "top_avoidable_nbhd_bucket_s": {f"{a}|{b}": round(v) for (a, b), v in avoidable.items()},
    }


def fit_speeds(world, k=30, lo=0.5, hi=2.0):
    calls = pd.DataFrame(load_calls())
    calls["bucket"] = calls["hour"].map(BUCKET_OF)
    calls["node"] = calls["call_id"].map(world.call_nodes)
    calls["home"] = calls["historical_unit"].map(world.usual)
    calls = calls.dropna(subset=["home"])
    calls["base"] = [float(world.travel[int(h), n]) for h, n in zip(calls["home"], calls["node"])]
    calls = calls[(calls["base"] > 60) & (calls["drive_s"] > 30)]
    train, holdout, frozen = (calls[calls["split"] == s] for s in ["train", "holdout", "frozen"])

    off = (train["drive_s"] - train["base"]).groupby(train["bucket"]).median()
    train = train.assign(r=(train["drive_s"] - train["bucket"].map(off)) / train["base"])
    cell = train.groupby(["nbhd", "bucket"])["r"].agg(["median", "size"])
    rel = {f"{nb}|{b}": round(float(min(max((row["size"] * row["median"] + k) / (row["size"] + k), lo), hi)), 4) for (nb, b), row in cell.iterrows()}

    def mae(df, use_rel):
        m = [rel.get(f"{nb}|{b}", 1.0) if use_rel else 1.0 for nb, b in zip(df["nbhd"], df["bucket"])]
        return float((df["base"] * m + df["bucket"].map(off) - df["drive_s"]).abs().median())

    report = {s: {"citywide_mae_s": round(mae(df, False), 1), "learned_mae_s": round(mae(df, True), 1)} for s, df in [("train", train), ("holdout", holdout), ("frozen", frozen)]}
    adopted = report["holdout"]["learned_mae_s"] < report["holdout"]["citywide_mae_s"]
    centers = calls.groupby("nbhd")[["lat", "lon"]].median()
    (DATA / "speeds.json").write_text(json.dumps({
        "adopted": adopted,
        "report": report,
        "rel": rel,
        "nbhd_centers": {nb: [round(r.lat, 5), round(r.lon, 5)] for nb, r in centers.iterrows()},
    }, indent=1))
    return adopted, report


def replay(calls, policy="baseline", slowdowns=None, version=None, split="train", world=None):
    world = world or World.load(slowdowns=slowdowns)
    results, _ = simulate(world, calls, policy)
    name = version or (Path(policy).stem if isinstance(policy, str) else getattr(policy, "__name__", "policy"))
    return results, summarize(results, name, split)


def calibrate(world, iters=10):
    calls = load_calls("fit")
    offsets = {b: 0.0 for b in BUCKETS}
    for i in range(iters):
        world.offsets = offsets
        results, _ = simulate(world, calls, "baseline")
        gaps = {}
        for b, hs in BUCKETS.items():
            rows = [r for r in results if r["hour"] in hs]
            gaps[b] = statistics.median(r["historical_drive_s"] for r in rows) - statistics.median(r["eta_s"] for r in rows)
        offsets = {b: offsets[b] + gaps[b] for b in BUCKETS}
        print(f"iter {i}: " + ", ".join(f"{b}={offsets[b]:+.0f}s" for b in BUCKETS))
        if all(abs(g) < 2 for g in gaps.values()):
            break
    world.offsets = offsets
    (DATA / "calibration.json").write_text(json.dumps({"offsets": offsets}, indent=1))
    return offsets


def calibration_report(world):
    out = {}
    for split in ["fit", "frozen"]:
        results, stats = simulate(world, load_calls(split), "baseline")
        sim = statistics.median(r["response_s"] for r in results)
        hist = statistics.median(r["historical_s"] for r in results)
        eta = [r["eta_s"] for r in results]
        real = [r["historical_drive_s"] for r in results]
        out[split] = {
            "sim_median_s": sim,
            "actual_median_s": hist,
            "error_pct": round(100 * (sim - hist) / hist, 2),
            "drive_spearman": round(float(spearmanr(eta, real).statistic), 3),
            "drive_median_abs_err_s": round(statistics.median(abs(x - y) for x, y in zip(eta, real))),
            **stats,
            "by_bucket_sim_vs_actual": {
                b: (round(statistics.median(r["response_s"] for r in results if r["hour"] in hs)), round(statistics.median(r["historical_s"] for r in results if r["hour"] in hs)))
                for b, hs in BUCKETS.items()
            },
        }
    return out


def self_check():
    travel = np.array([[0.0, 100.0, 400.0, 900.0], [400.0, 0.0, 100.0, 900.0]])
    back = travel.copy()

    def world_():
        return World(travel, back, np.array([0, 2]), {"c1": 3, "c2": 2, "c3": 2, "c4": 1}, {"A": 0, "B": 1}, {}, [], {b: 0.0 for b in BUCKETS})

    base = {"processing_s": 60.0, "turnout_s": 10.0, "drive_s": 100.0, "busy_s": 1800.0, "historical_s": 200.0, "hour": 12, "nbhd": "x", "lat": 0.0, "lon": 0.0}
    calls = [
        {**base, "call_id": "c2", "t": "2026-01-01T12:00:00"},
        {**base, "call_id": "c3", "t": "2026-01-01T12:01:00"},
        {**base, "call_id": "c4", "t": "2026-01-01T12:02:00"},
    ]

    class Base:
        @staticmethod
        def post_plan(hour, units):
            return {"A": 0, "B": 1}

        @staticmethod
        def choose_unit(call, idle, eta):
            return min(idle, key=lambda u: eta(u, call))["unit_id"]

    w = world_()
    r, stats = simulate(w, calls, Base)
    assert r[0]["unit"] == "B" and r[0]["eta_s"] == 100.0, r[0]
    assert r[1]["unit"] == "A" and r[1]["eta_s"] == 400.0, r[1]
    assert r[2]["wait_s"] > 0 and stats["queued"] == 1, r[2]
    assert r[0]["response_s"] == 60 + 10 + 100

    w = world_()
    w.offsets = {b: 50.0 for b in BUCKETS}
    assert simulate(w, calls[:1], Base)[0][0]["eta_s"] == 150.0

    w = world_()
    w.background = [(int(epoch(["2026-01-01T11:59:00"])[0]), int(epoch(["2026-01-01T13:00:00"])[0]), "B")]
    assert simulate(w, calls[:1], Base)[0][0]["unit"] == "A"

    w = world_()
    w.windows = {"A": ([0], [10]), "B": ([0], [2**40])}
    r4, _ = simulate(w, calls[:2], Base)
    assert r4[0]["unit"] == "B" and r4[1]["unit"] == "B" and r4[1]["wait_s"] > 0, r4

    class Mover(Base):
        @staticmethod
        def post_plan(hour, units):
            return {"A": 1, "B": 1}

    r5, s5 = simulate(world_(), calls[:1], Mover)
    assert s5["relocations"] == 1 and r5[0]["unit"] == "B", (r5, s5)

    class Swap(Base):
        @staticmethod
        def post_plan(hour, units):
            return {"A": 1, "B": 0}

    r9, s9 = simulate(world_(), calls[:1], Swap)
    assert s9["relocations"] == 2 and r9[0]["unit"] == "B" and r9[0]["from_post"] == 1 and r9[0]["wait_s"] == 0 and r9[0]["eta_s"] == 100.0, r9

    late = [{**calls[0], "t": "2026-01-01T12:20:00"}]
    r10, _ = simulate(world_(), late, Swap)
    assert r10[0]["unit"] == "A" and r10[0]["from_post"] == 1, r10

    class Bad(Base):
        @staticmethod
        def choose_unit(call, idle, eta):
            return "nope"

    assert simulate(world_(), calls[:1], Bad)[1]["invalid_choice"] == 1

    class AppendBusy(Base):
        @staticmethod
        def choose_unit(call, idle, eta):
            idle.append({"unit_id": "B", "post": 1})
            return "B"

    r7, s7 = simulate(world_(), calls[:2], AppendBusy)
    assert r7[1]["unit"] == "A" and s7["invalid_choice"] == 1, r7

    class Crash(Base):
        @staticmethod
        def post_plan(hour, units):
            return {"A": True, "B": 99}

        @staticmethod
        def choose_unit(call, idle, eta):
            idle.clear()
            raise RuntimeError

    r8, s8 = simulate(world_(), calls, Crash)
    assert len(r8) == 3 and s8["policy_errors"] == 2 and s8["relocations"] == 0, s8

    class Peek(Base):
        @staticmethod
        def choose_unit(call, idle, eta):
            assert call["call_id"].startswith("call") and "drive_s" not in call and "historical_unit" not in call
            assert not hasattr(eta, "__self__")
            return idle[0]["unit_id"]

    simulate(world_(), calls, Peek)

    for src in ["import os", "import sys", "from pathlib import Path", "x = eta.__self__", "f = open('x')", "import sim", "g = getattr(eta, 'a')"]:
        try:
            check_source(src)
            raise AssertionError(f"not rejected: {src}")
        except PolicyRejected:
            pass
    check_source((POLICIES / "baseline.py").read_text())

    w = world_()
    w.rel = {"x|midday": 1.5}
    assert simulate(w, calls[:1], Base)[0][0]["eta_s"] == 150.0
    eta = make_eta(w, {"call0": 2})
    assert eta({"unit_id": "B", "post": 1}, {"call_id": "call0", "hour": 12, "nbhd": "x"}) == 150.0

    rt, _ = simulate(world_(), calls, Base, trace=True)
    assert rt[0]["closest_post"] == 1 and rt[0]["closest_post_drive_s"] == 100.0 and rt[0]["n_idle"] == 2, rt[0]
    assert rt[1]["units_at_closest_post"] == {"B": "busy"}, rt[1]

    a = [{"call_id": str(i), "response_s": 100.0 + i} for i in range(200)]
    b = [{"call_id": str(i), "response_s": 130.0 + i} for i in range(200)]
    ci = paired_ci(a, b, n=200)
    assert ci["median_diff_s"] == -30 and ci["ci95"][1] < 0, ci

    roads = Roads()
    src = roads.nearest([37.7793], [-122.4193])
    plain = roads.times(src)
    slow = roads.times(src, {e: 3.0 for e in range(len(roads.edges))})
    far = roads.nearest([37.7599], [-122.4148])[0]
    assert np.isclose(slow[0, far], 3 * plain[0, far]) and 120 < plain[0, far] < 900, plain[0, far]
    assert not plain.flags.writeable
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
    ap.add_argument("--split", default="train", choices=list(SPLITS))
    ap.add_argument("--compare", metavar="BASELINE_POLICY")
    ap.add_argument("--out")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--fixtures", action="store_true")
    ap.add_argument("--worst", type=int, metavar="N")
    ap.add_argument("--fit-speeds", action="store_true")
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
    if a.fit_speeds:
        adopted, report = fit_speeds(world)
        print(json.dumps(report, indent=1))
        print("learned speeds adopted, recalibrating" if adopted else "learned speeds rejected: holdout error did not drop")
        if adopted:
            world = World.load(roads=world.roads)
            calibrate(world)
            print(json.dumps(calibration_report(world), indent=1))
        return
    calls = load_calls(a.split)
    if a.worst:
        out = worst_calls(world, calls, a.policy, a.worst)
        if a.out:
            Path(a.out).write_text(json.dumps(out, indent=1))
        print(json.dumps(out, indent=1))
        return
    try:
        results, stats = simulate(world, calls, a.policy)
    except PolicyRejected as e:
        raise SystemExit(f"rejected: {e}")
    summary = summarize(results, Path(a.policy).stem, a.split)
    summary["actual_median_s"] = statistics.median(r["historical_s"] for r in results)
    summary["stats"] = stats
    if a.compare:
        summary["vs_" + Path(a.compare).stem] = paired_ci(results, simulate(world, calls, a.compare)[0])
    if a.out:
        Path(a.out).write_text(json.dumps({"summary": summary, "results": results}))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
