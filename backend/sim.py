import argparse
import ast
import bisect
import importlib
import importlib.util
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import signal
import tempfile
import time
import types
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
CAMERA_RADIUS_M = 600
SLOW_N = 10
SLOW_MIN_CALLS = 50
OVERALL_TOLERANCE_S = 5.0
PUBLIC = ("t", "lat", "lon", "hour", "nbhd", "station_area", "priority", "life_threatening")
ALLOWED_IMPORTS = {"math", "statistics", "collections", "itertools", "functools", "heapq", "bisect", "random"}
BANNED_ATTRS = {"gi_frame", "gi_code", "gi_yieldfrom", "cr_frame", "cr_code", "cr_await", "ag_frame", "ag_code", "f_globals", "f_locals",
                "f_builtins", "f_back", "f_code", "tb_frame", "tb_next", "co_consts", "mro", "format", "format_map"}
POLICY_SEED = 0
CHILD_TIMEOUT_S = 90
CHILD_MAX_OUTPUT = 50_000_000
UNSANDBOXED_ENV = "GM_UNSANDBOXED"
BLOCKED_SERVICES = ["com.apple.coreservices.launchservicesd", "com.apple.lsd.mapdb", "com.apple.lsd.modifydb", "com.apple.coreservices.appleevents",
                    "com.apple.pasteboard.1", "com.apple.SecurityServer", "com.apple.securityd", "com.apple.tccd", "com.apple.windowserver.active",
                    "com.apple.coreservices.quarantine-resolver", "com.apple.SystemConfiguration.configd"]
BANNED_NAMES = {"open", "eval", "exec", "compile", "globals", "locals", "vars", "getattr", "setattr", "delattr", "__import__", "input", "breakpoint", "memoryview", "type", "object", "super"}


class PolicyRejected(Exception):
    pass


def epoch(ts):
    return pd.to_datetime(pd.Series(ts)).astype("datetime64[s]").astype("int64").to_numpy()


def xy(lat, lon):
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    return np.column_stack([lon * 111320 * np.cos(np.radians(37.77)), lat * 110540])


def _module_exports(name):
    mod = importlib.import_module(name)
    return {n for n in dir(mod) if not n.startswith("_") and not isinstance(getattr(mod, n), types.ModuleType)}


MODULE_EXPORTS = {m: _module_exports(m) for m in ALLOWED_IMPORTS}


def _root_name(node):
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


def check_source(src):
    """Static checks on agent-written policy code. Imported modules may only be used as module.function from a
    fixed export list, nothing may be stored as an attribute, and no classes or underscore names may be defined,
    so a policy can't reach sys/os or patch objects the simulator relies on."""
    tree = ast.parse(src)
    modules, imported = {}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                modules[a.asname or a.name] = a.name
        elif isinstance(node, ast.ImportFrom):
            imported.update(a.asname or a.name for a in node.names)
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad = [a.name for a in node.names if a.name not in ALLOWED_IMPORTS]
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in ALLOWED_IMPORTS:
                bad = [node.module or "."]
            else:
                bad = [a.name for a in node.names if a.name not in MODULE_EXPORTS[node.module]]
        elif isinstance(node, ast.ClassDef):
            bad = [f"class {node.name}"]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bad = [node.name] if node.name.startswith("__") else []
        elif isinstance(node, ast.arg):
            bad = [node.arg] if node.arg.startswith("__") else []
        elif isinstance(node, ast.Attribute):
            bad = [node.attr] if node.attr.startswith("_") or node.attr in BANNED_ATTRS else []
            if not bad and not isinstance(node.ctx, ast.Load):
                bad = [f"attribute assignment .{node.attr}"]
        elif isinstance(node, ast.Subscript):
            bad = [f"item assignment on {_root_name(node)}"] if not isinstance(node.ctx, ast.Load) and _root_name(node) in modules.keys() | imported else []
        elif isinstance(node, ast.Name):
            bad = [node.id] if node.id in BANNED_NAMES or node.id.startswith("__") else []
            if not bad and node.id in modules:
                up = parent.get(node)
                ok = isinstance(up, ast.Attribute) and up.value is node and up.attr in MODULE_EXPORTS[modules[node.id]]
                bad = [] if ok else [node.id]
            elif not bad and node.id in imported and not isinstance(node.ctx, ast.Load):
                bad = [f"reassigning imported {node.id}"]
        else:
            bad = []
        if bad:
            raise PolicyRejected(f"policy uses disallowed {bad[0]!r} (line {getattr(node, 'lineno', '?')})")


class SandboxUnavailable(Exception):
    pass


def needs_isolation(policy):
    """Everything except the shipped baseline runs sandboxed."""
    if not isinstance(policy, (str, Path)):
        return False
    if str(policy) == "baseline":
        return False
    path = Path(str(policy)) if str(policy).endswith(".py") else POLICIES / f"{policy}.py"
    return path.resolve() != (POLICIES / "baseline.py").resolve()


def policy_file(policy):
    policy = str(policy)
    return (Path(policy) if policy.endswith(".py") else POLICIES / f"{policy}.py").resolve()


def sandbox_profile(workdir, policy_path):
    """macOS seatbelt profile for a policy run: no network, no new processes or risky system services, writes
    only to workdir, and file contents readable only from Python, the simulator code and its data."""
    q = lambda p: str(Path(p).resolve()).replace("\\", "\\\\").replace('"', '\\"')
    here = Path(__file__).resolve().parent
    reads = [sys.prefix, sys.base_prefix, sys.exec_prefix, here, DATA, workdir, "/System", "/usr/lib", "/usr/share", "/private/var/db/timezone",
             "/private/var/db/dyld", "/Library/Apple", "/dev"]
    allow_read = " ".join(f'(subpath "{q(r)}")' for r in reads) + f' (literal "{q(policy_path)}") (literal "/private/etc/localtime") (literal "/")'
    services = " ".join(f'(global-name "{n}")' for n in BLOCKED_SERVICES)
    return f"""(version 1)
(allow default)
(deny network*)
(deny process-fork)
(deny process-exec*)
(allow process-exec* (subpath "{q(sys.base_prefix)}") (subpath "{q(sys.prefix)}"))
(deny mach-lookup {services})
(deny file-write*)
(allow file-write* (subpath "{q(workdir)}") (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr") (literal "/dev/dtracehelper"))
(deny file-read-data)
(allow file-read-data {allow_read})
"""


def child_env():
    keep = ("PATH", "LANG", "LC_ALL", "TMPDIR")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["HOME"] = tempfile.gettempdir()
    return env


def _clean_stale_workdirs(max_age_s=3600):
    now = time.time()
    for d in Path(tempfile.gettempdir()).glob("gm-policy-*"):
        try:
            if now - d.stat().st_mtime > max_age_s:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def record_decisions(policy, split, sim_kwargs=None):
    """Run an agent-written policy in a separate, sandboxed process and return only its decisions."""
    check_source(policy_file(policy).read_text())
    sandboxed = sys.platform == "darwin" and shutil.which("sandbox-exec")
    if not sandboxed and os.environ.get(UNSANDBOXED_ENV) != "1":
        raise SandboxUnavailable(f"no sandbox-exec on this machine; set {UNSANDBOXED_ENV}=1 to run agent policies without an OS sandbox")
    if not sandboxed:
        print(f"warning: running {policy} without an OS sandbox ({UNSANDBOXED_ENV}=1)", file=sys.stderr)
    _clean_stale_workdirs()
    workdir = Path(tempfile.mkdtemp(prefix="gm-policy-")).resolve()
    try:
        out = workdir / "decisions.json"
        cmd = [sys.executable, str(Path(__file__).resolve()), "--child", "--policy", str(policy_file(policy)), "--split", split,
               "--child-kwargs", json.dumps(sim_kwargs or {}), "--out", str(out)]
        if sandboxed:
            cmd = ["sandbox-exec", "-p", sandbox_profile(workdir, policy_file(policy))] + cmd
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=workdir, env=child_env(), start_new_session=True)
        try:
            code = proc.wait(timeout=CHILD_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise PolicyRejected(f"policy run timed out after {CHILD_TIMEOUT_S}s")
        if code != 0 or not out.exists() or out.stat().st_size > CHILD_MAX_OUTPUT:
            raise PolicyRejected(f"policy run crashed (exit {code})")
        try:
            return json.loads(out.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise PolicyRejected("policy run returned unreadable output")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


ERROR = {"error": 1}


class Recorder:
    """Child side: wraps the policy, hands the simulator sanitized decisions and logs exactly those."""

    def __init__(self, policy):
        self.policy = policy
        self.log = {"plans": [], "choices": [], "engines": [], "has_send_engine": hasattr(policy, "send_engine")}
        if self.log["has_send_engine"]:
            self.send_engine = self._send_engine

    def post_plan(self, hour, units):
        try:
            plan = {u: int(p) for u, p in dict(self.policy.post_plan(hour, units)).items() if type(u) is str}
        except BaseException:
            self.log["plans"].append(ERROR)
            raise RuntimeError("post_plan failed")
        self.log["plans"].append(plan)
        return plan

    def choose_unit(self, call, idle, eta):
        try:
            chosen = self.policy.choose_unit(call, idle, eta)
        except BaseException:
            self.log["choices"].append(ERROR)
            raise RuntimeError("choose_unit failed")
        chosen = chosen if type(chosen) is str else None
        self.log["choices"].append(chosen)
        return chosen

    def _send_engine(self, call, engines, eta, ambulance_s):
        try:
            sent = self.policy.send_engine(call, engines, eta, ambulance_s)
        except BaseException:
            self.log["engines"].append(ERROR)
            raise RuntimeError("send_engine failed")
        sent = sent if sent is None or type(sent) is str else "\x00invalid"
        self.log["engines"].append(sent)
        return sent


class Replay:
    """Parent side: feeds recorded decisions back to the simulator in order. Only plain str/int values survive."""

    def __init__(self, log):
        plans = [p if p == ERROR else {u: v for u, v in p.items() if type(u) is str and type(v) is int}
                 for p in log.get("plans", []) if isinstance(p, dict)]
        pick = lambda xs: [x if x == ERROR or x is None or type(x) is str else None for x in xs] if isinstance(xs, list) else []
        self._plans, self._choices, self._engines = iter(plans), iter(pick(log.get("choices"))), iter(pick(log.get("engines")))
        if log.get("has_send_engine") is True:
            self.send_engine = self._send_engine

    @staticmethod
    def _next(it, default):
        x = next(it, default)
        if x == ERROR:
            raise RuntimeError("policy raised")
        return x

    def post_plan(self, hour, units):
        return self._next(self._plans, {})

    def choose_unit(self, call, idle, eta):
        return self._next(self._choices, None)

    def _send_engine(self, call, engines, eta, ambulance_s):
        return self._next(self._engines, None)


def run_isolated(policy, split, world, calls, **sim_kwargs):
    """Simulate an agent-written policy without ever importing it here: the sandboxed child records its decisions
    and this process replays them, so every scored number comes from simulator code."""
    return simulate(world, calls, Replay(record_decisions(policy, split, sim_kwargs)), **sim_kwargs)


class Roads:
    def __init__(self, path=DATA / "sf.graphml"):
        import osmnx as ox

        g = ox.load_graphml(path)
        nodes = list(g.nodes)
        idx = {n: i for i, n in enumerate(nodes)}
        self.n = len(nodes)
        self.node_ll = np.array([[g.nodes[n]["y"], g.nodes[n]["x"]] for n in nodes])
        self.node_xy = xy(self.node_ll[:, 0], self.node_ll[:, 1])
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

    def path(self, src, dst, slowdowns=None):
        dist, pred = dijkstra(self._matrix(slowdowns), directed=True, indices=int(src), return_predecessors=True)
        out = [int(dst)]
        while out[-1] != src and pred[out[-1]] >= 0:
            out.append(int(pred[out[-1]]))
        return float(dist[dst]), [[round(a, 6), round(b, 6)] for a, b in self.node_ll[out[::-1]]]

    def times(self, sources, slowdowns=None, reverse=False):
        key = (tuple(int(s) for s in sources), tuple(sorted((slowdowns or {}).items())), reverse)
        if key not in self._cache:
            t = dijkstra(self._matrix(slowdowns, reverse), directed=True, indices=list(sources))
            straight = np.linalg.norm(self.node_xy[None, :, :] - self.node_xy[list(sources)][:, None, :], axis=2)
            out = np.where(np.isfinite(t), t, straight * DETOUR / FALLBACK_MPS)
            out.setflags(write=False)
            self._cache[key] = out
        return self._cache[key]


def edge_slowdowns(readings, roads):
    mid = (roads.node_xy[roads.edge_u] + roads.node_xy[roads.edge_v]) / 2
    refs = [set(str(r or "").replace(";", ",").split(",")) for r in roads.edge_ref]
    out = {}
    for r in readings:
        f = float(r.get("slowdown", 1.0))
        if f <= 1.0:
            continue
        route = r.get("route", "").replace("-", " ").replace("SR ", "CA ")
        near = np.linalg.norm(mid - xy([r["lat"]], [r["lon"]]), axis=1) <= CAMERA_RADIUS_M
        for e in np.flatnonzero(near):
            if not route or route in refs[e]:
                out[int(e)] = max(out.get(int(e), 1.0), f)
    return out


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
    mod.ENGINES = dict(world.engines)
    mod.ENGINE_LOAD = dict(world.engine_load)
    mod.WEATHER = {k: dict(v) for k, v in world.weather.items()}
    mod.NBHD = {k: dict(v) for k, v in world.nbhd_info.items()}
    random.seed(POLICY_SEED)
    spec.loader.exec_module(mod)
    return mod


class World:
    def __init__(self, travel, back, post_nodes, call_nodes, homes, windows, background, offsets, posts=(), rel=None, end_nodes=None):
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
        self.end_nodes = end_nodes or {}
        self.engines = {}
        self.engine_background = []
        self.engine_offsets = {b: 0.0 for b in BUCKETS}
        self.engine_on_scene = 0.0
        self.engine_load = {}
        self.weather = {}
        self.nbhd_info = {}

    @classmethod
    def load(cls, roads=None, slowdowns=None, offsets=None):
        roads = roads or Roads()
        posts = json.loads((DATA / "posts.json").read_text())
        homes = json.loads((DATA / "units.json").read_text())
        calls = pd.read_parquet(DATA / "calls.parquet")
        dispatches = pd.read_parquet(DATA / "dispatches.parquet")
        post_nodes = roads.nearest([p["lat"] for p in posts], [p["lon"] for p in posts])
        call_nodes = dict(zip(calls["call_id"], roads.nearest(calls["lat"], calls["lon"])))
        hospitals = [h for h in json.loads((DATA / "hospitals.json").read_text()) if h.get("general", True)]
        h_xy = xy([h["lat"] for h in hospitals], [h["lon"] for h in hospitals])
        h_nodes = roads.nearest([h["lat"] for h in hospitals], [h["lon"] for h in hospitals])
        nearest_h = np.linalg.norm(xy(calls["lat"], calls["lon"])[:, None, :] - h_xy[None, :, :], axis=2).argmin(axis=1)
        end_nodes = {cid: int(h_nodes[h]) if tr else int(call_nodes[cid]) for cid, tr, h in zip(calls["call_id"], calls["transported"], nearest_h)}
        bg = dispatches[~dispatches["simulated"]]
        background = sorted(zip(epoch(bg["start"]), epoch(bg["end"]), bg["unit_id"]))
        world = cls(
            roads.times(post_nodes, slowdowns),
            roads.times(post_nodes, slowdowns, reverse=True),
            post_nodes, call_nodes, homes, shift_windows(dispatches), background,
            offsets or load_offsets(), posts, load_rel(), end_nodes,
        )
        world.roads = roads
        world.usual = json.loads((DATA / "unit_areas.json").read_text())
        for attr, name in (("weather", "weather.json"), ("nbhd_info", "neighborhoods.json")):
            if (DATA / name).exists():
                setattr(world, attr, json.loads((DATA / name).read_text()))
        if (DATA / "engines.json").exists():
            ej = json.loads((DATA / "engines.json").read_text())
            world.engines = {e["unit_id"]: e["post"] for e in ej["engines"]}
            world.engine_on_scene = ej["on_scene_s"]
            ed = pd.read_parquet(DATA / "engine_dispatches.parquet")
            ed = ed[~ed["simulated"]]
            world.engine_background = sorted(zip(epoch(ed["start"]), epoch(ed["end"]), ed["unit_id"]))
            world.engine_offsets = fit_engine_offsets(world)
            frozen = epoch([calls.loc[calls["split"] == "frozen", "t"].min()])[0]
            fit = ed[epoch(ed["start"]) < frozen]
            days = (frozen - epoch([calls["t"].min()])[0]) / 86400
            hours = pd.to_datetime(fit["start"]).dt.hour
            world.engine_load = {f"{u}|{h}": round(n / days, 3) for (u, h), n in fit.groupby([fit["unit_id"], hours]).size().items()}
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

    def engine_drive(self, post, node, hour, nbhd=None):
        return float(self.travel[post, node]) * self.mult(hour, nbhd) + self.engine_offsets[BUCKET_OF[hour]]

    def move_time(self, a, b, hour):
        return float(self.travel[a, self.post_nodes[b]])


def fit_engine_offsets(world):
    gaps = {b: [] for b in BUCKETS}
    for c in load_calls("fit"):
        u = c.get("engine_unit")
        if u in world.engines and c["engine_s"] == c["engine_s"]:
            raw = float(world.travel[world.engines[u], world.call_nodes[c["call_id"]]]) * world.mult(c["hour"], c["nbhd"])
            gaps[BUCKET_OF[c["hour"]]].append(c["engine_s"] - c["processing_s"] - raw)
    return {b: float(statistics.median(g)) if g else 0.0 for b, g in gaps.items()}


def make_eta(world, nodes):
    travel = world.travel
    offsets = dict(world.offsets)
    engine_offsets = dict(world.engine_offsets)
    rel = dict(world.rel)
    n_posts = len(world.post_nodes)

    def eta(unit, call):
        post = int(unit["post"])
        if not 0 <= post < n_posts:
            raise ValueError(f"no post {post}")
        b = BUCKET_OF[int(call["hour"])]
        off = engine_offsets if unit.get("kind") == "engine" else offsets
        return float(travel[post, nodes[call["call_id"]]]) * rel.get(f"{call.get('nbhd')}|{b}", 1.0) + off[b]

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


def simulate(world, calls, policy, trace=False, offload_cap_s=None, dispatch_at_entry=False, engines=False):
    policy = load_policy(policy, world)
    random.seed(POLICY_SEED)
    n_posts = len(world.post_nodes)
    busy = {u: 0 for u in world.units}
    post = dict(world.homes)
    bg = world.background
    bi = 0
    current_hour = None
    results = []
    stats = {"queued": 0, "invalid_choice": 0, "policy_errors": 0, "relocations": 0}
    if engines:
        stats.update(engine_sends=0, engine_conflicts=0)
        ebusy = {u: 0 for u in world.engines}
        esim = dict(ebusy)
        ebg = world.engine_background
        ei = 0
    tokens = {}
    eta = make_eta(world, tokens)
    prev = dict(post)
    arrive = {u: 0 for u in world.units}
    processing = [min(c["processing_s"], c["entry_s"]) if dispatch_at_entry and c.get("entry_s") == c.get("entry_s") and c.get("entry_s") is not None and c["entry_s"] >= 0 else c["processing_s"] for c in calls]
    dispatch_t = epoch([c["t"] for c in calls]) + np.array(processing)
    for i in np.argsort(dispatch_t, kind="stable"):
        c, t = calls[i], int(dispatch_t[i])
        proc = processing[i]
        pos = lambda u, when: post[u] if when >= arrive[u] else prev[u]
        at = lambda u: pos(u, t)
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
        start = float(max(busy[chosen], t))
        wait = start - t
        origin = pos(chosen, start)
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
        response = proc + wait + c["turnout_s"] + drive
        after = max(c["busy_s"] - c["turnout_s"] - c["drive_s"], MIN_ON_SCENE_S)
        hosp = c.get("hospital_s")
        if offload_cap_s is not None and hosp == hosp and hosp is not None and hosp > offload_cap_s:
            after = max(after - (hosp - offload_cap_s), MIN_ON_SCENE_S)
        end = world.end_nodes.get(c["call_id"], node)
        busy[chosen] = start + c["turnout_s"] + drive + after
        prev[chosen] = int(np.argmin(world.travel[:, end]))
        arrive[chosen] = busy[chosen] + world.return_drive(post[chosen], end, c["hour"], c["nbhd"])
        extra = {}
        if engines:
            while ei < len(ebg) and ebg[ei][0] <= t:
                s, e, u = ebg[ei]
                if esim.get(u, 0) > s:
                    stats["engine_conflicts"] += 1
                if u in ebusy:
                    ebusy[u] = max(ebusy[u], e)
                ei += 1
            idle_e = [u for u in world.engines if ebusy[u] <= t]
            sent = None
            if hasattr(policy, "send_engine"):
                try:
                    sent = policy.send_engine(public, [{"unit_id": u, "post": world.engines[u], "kind": "engine"} for u in idle_e], eta, round(wait + c["turnout_s"] + drive, 1))
                except Exception:
                    stats["policy_errors"] += 1
                    sent = None
                if sent is not None and sent not in idle_e:
                    stats["invalid_choice"] += 1
                    sent = None
            elif idle_e and c.get("engine_s") == c.get("engine_s") and c.get("engine_s") is not None:
                sent = min(idle_e, key=lambda u: world.engine_drive(world.engines[u], node, c["hour"], c["nbhd"]))
            engine_resp = None
            if sent:
                ed = world.engine_drive(world.engines[sent], node, c["hour"], c["nbhd"])
                engine_resp = proc + ed
                ebusy[sent] = esim[sent] = t + ed + world.engine_on_scene
                stats["engine_sends"] += 1
            hist_e = c.get("engine_s")
            extra = {
                "ambulance_s": round(response, 1),
                "engine_unit": sent,
                "engine_s": None if engine_resp is None else round(engine_resp, 1),
                "engine_post": world.engines[sent] if sent else None,
                "engine_eta_s": round(ed, 1) if sent else None,
                "historical_first_s": min(c["historical_s"], hist_e) if hist_e == hist_e and hist_e is not None else c["historical_s"],
            }
            if trace:
                extra["closest_idle_engine_s"] = round(min((world.engine_drive(world.engines[u], node, c["hour"], c["nbhd"]) for u in idle_e), default=float("nan")), 1)
            if engine_resp is not None:
                response = min(response, engine_resp)
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
            **extra,
            **(ctx if trace else {}),
        })
    return results, stats


def load_slow():
    path = DATA / "slow_nbhds.json"
    return json.loads(path.read_text())["nbhds"] if path.exists() else []


def pick_slow(world):
    results, _ = simulate(world, load_calls("train"), "baseline")
    by = {}
    for r in results:
        by.setdefault(r["nbhd"], []).append(r["response_s"])
    ranked = sorted(((statistics.median(v), k, len(v)) for k, v in by.items() if len(v) >= SLOW_MIN_CALLS), reverse=True)
    picked = [{"nbhd": k, "baseline_train_median_s": round(m), "train_calls": n} for m, k, n in ranked[:SLOW_N]]
    (DATA / "slow_nbhds.json").write_text(json.dumps({"nbhds": [p["nbhd"] for p in picked], "detail": picked}, indent=1))
    return picked


def summarize(results, version, split, slow=None):
    slow = set(load_slow() if slow is None else slow)
    r = sorted(x["response_s"] for x in results)
    sr = [x["response_s"] for x in results if x["nbhd"] in slow]
    return {
        "version": version,
        "split": split,
        "slow_median_s": statistics.median(sr) if sr else None,
        "slow_n": len(sr),
        "median_s": statistics.median(r),
        "mean_s": round(statistics.mean(r), 1),
        "p90_s": r[int(0.9 * (len(r) - 1))],
        "pct_under_8min": sum(x <= 480 for x in r) / len(r),
        "n": len(r),
    }


def goal(a, b, slow=None):
    slow = set(load_slow() if slow is None else slow)
    s_ci = paired_ci([r for r in a if r["nbhd"] in slow], [r for r in b if r["nbhd"] in slow])
    all_ci = paired_ci(a, b)
    return {
        "slow_nbhds": s_ci,
        "everyone": all_ci,
        "passes": s_ci["ci95"][1] < 0 and all_ci["median_diff_s"] <= OVERALL_TOLERANCE_S,
    }


def paired_ci(a, b, n=1000, seed=0):
    by_id = {r["call_id"]: r["response_s"] for r in b}
    pairs = [(r["response_s"], by_id[r["call_id"]]) for r in a if r["call_id"] in by_id]
    if not pairs:
        return {"median_diff_s": 0.0, "ci95": [0.0, 0.0], "n": 0}
    rng = random.Random(seed)
    diffs = []
    for _ in range(n):
        s = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        diffs.append(statistics.median(x for x, _ in s) - statistics.median(y for _, y in s))
    diffs.sort()
    point = statistics.median(x for x, _ in pairs) - statistics.median(y for _, y in pairs)
    return {"median_diff_s": point, "ci95": [diffs[int(0.025 * n)], diffs[int(0.975 * n) - 1]], "n": len(pairs)}


def worst_calls(world, calls, policy, n=50, engines=False):
    results, stats = simulate(world, calls, policy, trace=True, engines=engines)
    for r in results:
        r["controllable_s"] = round(r["wait_s"] + r["eta_s"], 1)
        r["weekday"] = pd.Timestamp(r["t"]).day_name()
        r["bucket"] = BUCKET_OF[r["hour"]]
        r["avoidable_s"] = round(max(r["controllable_s"] - r["closest_post_drive_s"], 0), 1)
    worst = sorted(results, key=lambda r: -(r["response_s"] - r["processing_s"] if engines else r["controllable_s"]))[:n]
    keep = (["response_s", "ambulance_s", "engine_unit", "engine_s", "closest_idle_engine_s"] if engines else []) + ["t", "weekday", "hour", "bucket", "nbhd", "station_area", "priority", "controllable_s", "wait_s", "eta_s", "avoidable_s", "unit", "from_post", "from_post_area",
            "closest_post", "closest_post_area", "closest_post_drive_s", "units_at_closest_post", "n_idle", "n_on_duty"]
    calls_by_id = {c["call_id"]: c for c in calls}
    rows = [{k: (calls_by_id[r["call_id"]].get(k) if k in ("station_area", "priority") else r.get(k)) for k in keep} for r in worst]
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
    json.dumps(r)
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

    clear_travel = np.array([[0.0, 100.0, 400.0, 900.0], [400.0, 0.0, 100.0, 50.0]])
    wc = World(clear_travel, clear_travel.copy(), np.array([0, 1]), {"k1": 2, "k2": 2, "k3": 2}, {"A": 0}, {}, [], {b: 0.0 for b in BUCKETS}, end_nodes={"k1": 3})
    kc = [
        {**base, "call_id": "k1", "t": "2026-01-01T12:00:00"},
        {**base, "call_id": "k2", "t": "2026-01-01T12:37:00"},
        {**base, "call_id": "k3", "t": "2026-01-01T13:30:00"},
    ]

    class Home(Base):
        @staticmethod
        def post_plan(hour, units):
            return {"A": 0}

    rk, _ = simulate(wc, kc, Home)
    assert rk[0]["from_post"] == 0 and rk[0]["eta_s"] == 400.0, rk[0]
    assert rk[1]["from_post"] == 1 and rk[1]["eta_s"] == 100.0 and rk[1]["wait_s"] == 0, rk[1]
    assert rk[2]["from_post"] == 0 and rk[2]["eta_s"] == 400.0, rk[2]

    kh = [{**kc[0], "hospital_s": 1500.0}, kc[1], kc[2]]
    rh, _ = simulate(wc, kh, Home, offload_cap_s=1200.0)
    assert rh[1]["wait_s"] == 0 and rh[1]["from_post"] == 1, rh[1]
    rq, _ = simulate(wc, [{**kh[0]}, {**kc[1], "t": "2026-01-01T12:32:00"}], Home)
    rr, _ = simulate(wc, [{**kh[0]}, {**kc[1], "t": "2026-01-01T12:32:00"}], Home, offload_cap_s=1200.0)
    assert rq[1]["wait_s"] > 0 and rr[1]["wait_s"] == 0, (rq[1], rr[1])

    ke = [{**kc[0], "entry_s": 20.0}]
    assert simulate(wc, ke, Home, dispatch_at_entry=True)[0][0]["response_s"] == 20 + 10 + 400
    assert simulate(wc, ke, Home)[0][0]["response_s"] == 60 + 10 + 400

    we = world_()
    we.engines = {"E1": 1}
    we.engine_on_scene = 600.0
    ec = [{**calls[0], "engine_s": 150.0}, {**calls[1], "engine_s": float("nan")}]
    rd, sd = simulate(we, ec, Base, engines=True)
    assert rd[0]["engine_unit"] == "E1" and rd[0]["response_s"] == 160 and rd[0]["ambulance_s"] == 170 and rd[0]["historical_first_s"] == 150, rd[0]
    assert rd[1]["engine_unit"] is None and rd[1]["response_s"] == rd[1]["ambulance_s"] and sd["engine_sends"] == 1, rd[1]
    assert "engine_s" not in simulate(we, ec, Base)[0][0]

    class Engine(Base):
        @staticmethod
        def send_engine(call, engines, eta, ambulance_s):
            assert engines[0]["kind"] == "engine" and eta(engines[0], call) == 100.0 and ambulance_s == 110.0
            return engines[0]["unit_id"]

    we.engine_background = [(int(epoch(["2026-01-01T12:02:00"])[0]), int(epoch(["2026-01-01T12:30:00"])[0]), "E1")]
    re_, se = simulate(we, ec + [{**calls[2], "t": "2026-01-01T12:03:00"}], Engine, engines=True)
    assert se["engine_sends"] == 1 and se["engine_conflicts"] == 1 and re_[1]["engine_unit"] is None, (re_, se)

    class BadEngine(Base):
        @staticmethod
        def send_engine(call, engines, eta, ambulance_s):
            return "E9"

    assert simulate(we, ec[:1], BadEngine, engines=True)[1]["invalid_choice"] == 1
    eng_calls = ec + [{**calls[2], "t": "2026-01-01T12:03:00"}]
    for pol in (Base, Engine, BadEngine):
        rec = Recorder(pol)
        direct = simulate(we, eng_calls, rec, engines=True)
        replayed = simulate(we, eng_calls, Replay(json.loads(json.dumps(rec.log))), engines=True)
        assert json.dumps(direct, default=str) == json.dumps(replayed, default=str), (pol, direct, replayed)

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

    for src in ["from statistics import sys", "import statistics\nx = statistics.sys", "import statistics as s\nt = s", "import collections.abc",
                "from . import sim", "g = (x for x in [1])\nf = g.gi_frame", "x = '{0.gi_frame}'.format(1)",
                "import statistics\nstatistics.median = min", "import bisect\ndel bisect.bisect_right", "import random\nrandom.Random = None",
                "from collections import Counter\nCounter.most_common = None", "from statistics import median\nmedian = min",
                "from collections import Counter\nC = Counter\nC.most_common = None", "import random\nR = random.Random\nR.random = None",
                "class S(str):\n    pass", "def __eq__(a, b):\n    return True", "def f(__x):\n    return 1",
                "import os", "import sys", "from pathlib import Path", "x = eta.__self__", "f = open('x')", "import sim", "g = getattr(eta, 'a')"]:
        try:
            check_source(src)
            raise AssertionError(f"not rejected: {src}")
        except PolicyRejected:
            pass
    check_source((POLICIES / "baseline.py").read_text())
    check_source("import math\nimport random\nfrom collections import Counter, defaultdict\nrandom.seed(1)\nr = random.Random(2)\nx = math.sqrt(4) + Counter('ab')['a']")
    assert needs_isolation("v001") and needs_isolation("/tmp/x/baseline.py") and not needs_isolation("baseline")
    assert not needs_isolation(POLICIES / "baseline.py")
    check_source("def _helper(a):\n    return a\nfor _ in range(2):\n    pass")

    class Rand(Base):
        @staticmethod
        def choose_unit(call, idle, eta):
            if random.random() < 0.2:
                raise ValueError
            return random.choice(idle)["unit_id"] if random.random() < 0.5 else "nope"

    for pol in (Base, Rand, Crash, Mover):
        rec = Recorder(pol)
        direct = simulate(world_(), calls, rec)
        replayed = simulate(world_(), calls, Replay(json.loads(json.dumps(rec.log))))
        assert direct == replayed, (pol, direct, replayed)

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

    base = [{"call_id": str(i), "nbhd": "far" if i < 60 else "near", "response_s": 600.0 + i % 50} for i in range(300)]
    better = [{**r, "response_s": r["response_s"] - 40 if r["nbhd"] == "far" else r["response_s"] + 2} for r in base]
    worse = [{**r, "response_s": r["response_s"] - 40 if r["nbhd"] == "far" else r["response_s"] + 30} for r in base]
    assert goal(better, base, ["far"])["passes"] and not goal(worse, base, ["far"])["passes"]
    sm = summarize(better, "v", "x", ["far"])
    assert sm["slow_n"] == 60 and sm["slow_median_s"] < summarize(base, "b", "x", ["far"])["slow_median_s"]

    roads = Roads()
    src = roads.nearest([37.7793], [-122.4193])
    plain = roads.times(src)
    slow = roads.times(src, {e: 3.0 for e in range(len(roads.edges))})
    far = roads.nearest([37.7599], [-122.4148])[0]
    assert np.isclose(slow[0, far], 3 * plain[0, far]) and 120 < plain[0, far] < 900, plain[0, far]
    assert not plain.flags.writeable
    d, line = roads.path(src[0], far)
    assert np.isclose(d, plain[0, far]) and len(line) > 2 and np.allclose(line[0], roads.node_ll[src[0]], atol=1e-5), (d, line[:2])
    cams = json.loads((DATA / "cameras.json").read_text())
    tv = next(c for c in cams if c["route"] == "US-101")
    slow = edge_slowdowns([{**tv, "slowdown": 4.0}], roads)
    assert slow and all(v == 4.0 for v in slow.values()) and all("US 101" in str(roads.edge_ref[e]) for e in slow), len(slow)
    assert edge_slowdowns([{**tv, "slowdown": 1.0}], roads) == {}
    print("self-check ok")


def jam_test(world, camera_id, factor=4.0, split="holdout"):
    cams = {c["camera_id"]: c for c in json.loads((DATA / "cameras.json").read_text())}
    cam = cams[camera_id]
    slow = edge_slowdowns([{**cam, "slowdown": factor}], world.roads)
    jammed = World.load(roads=world.roads, slowdowns=slow)
    posts = sorted(set(world.homes.values()))
    rows = []
    for c in load_calls(split):
        node = world.call_nodes[c["call_id"]]
        plain = {p: world.drive(p, node, c["hour"], c["nbhd"]) for p in posts}
        jam = {p: jammed.drive(p, node, c["hour"], c["nbhd"]) for p in posts}
        blind = min(posts, key=plain.get)
        aware = min(posts, key=jam.get)
        if jam[blind] > plain[blind] + 1:
            rows.append({"t": c["t"], "nbhd": c["nbhd"], "blind_post": blind, "blind_eta_s": round(jam[blind], 1), "aware_post": aware, "aware_eta_s": round(jam[aware], 1), "saved_s": round(jam[blind] - jam[aware], 1), "jam_cost_s": round(jam[blind] - plain[blind], 1)})
    switched = [r for r in rows if r["aware_post"] != r["blind_post"]]
    return {
        "camera": cam["name"],
        "slowdown": factor,
        "edges_slowed": len(slow),
        "split": split,
        "calls_whose_best_route_crosses_the_jam": len(rows),
        "calls_sent_from_a_different_post": len(switched),
        "median_jam_cost_s": round(statistics.median(r["jam_cost_s"] for r in rows), 1) if rows else 0.0,
        "median_saved_by_switching_s": round(statistics.median(r["saved_s"] for r in switched), 1) if switched else 0.0,
        "examples": sorted(switched, key=lambda r: -r["saved_s"])[:5],
    }


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
    ap.add_argument("--offload-cap", type=float, metavar="MINUTES")
    ap.add_argument("--dispatch-at-entry", action="store_true")
    ap.add_argument("--engines", action="store_true")
    ap.add_argument("--jam-test", metavar="CAMERA_ID")
    ap.add_argument("--slowdown", type=float, default=4.0)
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--child-kwargs", default="{}", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.child:
        return child_main(a)
    if a.self_check:
        return self_check()
    world = World.load()
    if a.calibrate:
        calibrate(world)
        print(json.dumps(calibration_report(world), indent=1))
        print(json.dumps(pick_slow(world), indent=1))
        return
    if a.fixtures:
        return write_fixtures(world)
    if a.jam_test:
        print(json.dumps(jam_test(world, a.jam_test, a.slowdown, a.split if a.split != "train" else "holdout"), indent=1))
        return
    if a.fit_speeds:
        adopted, report = fit_speeds(world)
        print(json.dumps(report, indent=1))
        print("learned speeds adopted, recalibrating" if adopted else "learned speeds rejected: holdout error did not drop")
        if adopted:
            world = World.load(roads=world.roads)
            calibrate(world)
            print(json.dumps(calibration_report(world), indent=1))
            print(json.dumps(pick_slow(world), indent=1))
        return
    calls = load_calls(a.split)

    def run(policy, **kw):
        """Agent-written policies are recorded in a sandboxed child and replayed here; the baseline runs directly."""
        try:
            if needs_isolation(policy):
                return run_isolated(policy, a.split, world, calls, **kw)
            return simulate(world, calls, policy, **kw)
        except PolicyRejected as e:
            raise SystemExit(f"rejected: {e}")
        except SandboxUnavailable as e:
            raise SystemExit(str(e))

    if a.offload_cap is not None or a.dispatch_at_entry:
        base, _ = run(a.policy, engines=a.engines)
        cap = a.offload_cap * 60 if a.offload_cap is not None else None
        capped, _ = run(a.policy, offload_cap_s=cap, dispatch_at_entry=a.dispatch_at_entry, engines=a.engines)
        print(json.dumps({
            "offload_cap_min": a.offload_cap,
            "dispatch_at_entry": a.dispatch_at_entry,
            "policy": Path(a.policy).stem,
            "split": a.split,
            "now": summarize(base, "now", a.split),
            "capped": summarize(capped, "capped", a.split),
            "vs_now": goal(capped, base),
        }, indent=1))
        return
    if a.worst:
        if needs_isolation(a.policy):
            try:
                out = worst_calls(world, calls, Replay(record_decisions(a.policy, a.split, {"engines": a.engines})), a.worst, a.engines)
            except PolicyRejected as e:
                raise SystemExit(f"rejected: {e}")
            except SandboxUnavailable as e:
                raise SystemExit(str(e))
            out["summary"]["version"] = Path(str(a.policy)).stem
        else:
            out = worst_calls(world, calls, a.policy, a.worst, a.engines)
        if a.out:
            Path(a.out).write_text(json.dumps(out, indent=1))
        print(json.dumps(out, indent=1))
        return
    results, stats = run(a.policy, engines=a.engines)
    compare = run(a.compare, engines=a.engines)[0] if a.compare else None
    summary = summarize(results, Path(a.policy).stem, a.split)
    summary["actual_median_s"] = statistics.median(r["historical_s"] for r in results)
    if a.engines:
        summary["actual_first_median_s"] = statistics.median(r["historical_first_s"] for r in results)
        summary["ambulance_median_s"] = statistics.median(r["ambulance_s"] for r in results)
    summary["stats"] = stats
    if a.compare:
        summary["vs_" + Path(a.compare).stem] = goal(results, compare)
    if a.out:
        Path(a.out).write_text(json.dumps({"summary": summary, "results": results}, default=_json_default))
    print(json.dumps(summary, indent=1))


def _json_default(o):
    return o.item() if hasattr(o, "item") else str(o)


def child_main(a):
    """Inside the sandbox: run one policy and write only its decisions for the parent to replay."""
    kwargs = json.loads(a.child_kwargs)
    world = World.load()
    calls = load_calls(a.split)
    rec = Recorder(load_policy(a.policy, world))
    simulate(world, calls, rec, **{k: kwargs[k] for k in ("offload_cap_s", "dispatch_at_entry", "engines") if k in kwargs})
    Path(a.out).write_text(json.dumps(rec.log))


if __name__ == "__main__":
    main()
