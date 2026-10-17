"""FastAPI for the UI. Serves the agent's run log and replays from backend/runs/, falling back to fixtures/
when a run hasn't happened yet so the frontend works on a fresh clone.

    uvicorn api:app --reload --port 8000     (from backend/)
"""

import asyncio
import json
import re
import threading
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

BACKEND = Path(__file__).parent
ROOT = BACKEND.parent
RUNS = BACKEND / "runs"
DATA = ROOT / "data"
FIXTURES = ROOT / "fixtures"
POLICIES = BACKEND / "policies"
VERSION_RE = re.compile(r"^(baseline|v\d{3})$")
SPLITS = {"train", "holdout", "frozen"}
LIVE_POLL_S = 2.0

app = FastAPI(title="Golden Minute")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
_sim_lock = threading.Lock()


def read_json(*paths):
    for p in paths:
        if p.exists():
            return json.loads(p.read_text()), p
    return None, None


def read_jsonl(*paths):
    for p in paths:
        if p.exists():
            return [json.loads(line) for line in p.read_text().splitlines() if line.strip()], p
    return [], None


def source(path):
    return "fixtures" if path and FIXTURES in path.parents else "live"


@lru_cache(maxsize=1)
def world():
    import sim

    return sim.World.load()


def simulate_results(version, split):
    cache = RUNS / f"replay_{version}_{split}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    if not (DATA / "calls.parquet").exists() or not (DATA / "sf.graphml").exists():
        return None
    import sim

    with _sim_lock:
        results, _ = sim.simulate(world(), sim.load_calls(split), version)
    RUNS.mkdir(exist_ok=True)
    cache.write_text(json.dumps(results))
    return results


@app.get("/health")
def health():
    return {"ok": True, "data": (DATA / "calls.parquet").exists(), "graph": (DATA / "sf.graphml").exists()}


@app.get("/versions")
def versions():
    rows, path = read_jsonl(RUNS / "versions.jsonl", FIXTURES / "versions.jsonl")
    return {"source": source(path), "versions": rows}


@app.get("/headline")
def headline():
    data, path = read_json(RUNS / "headline.json")
    if data:
        return {"source": "live", **data}
    summary, path = read_json(FIXTURES / "summary.json")
    return {"source": "fixtures", "version": None, "baseline_median_s": summary["median_s"] if summary else None, "agent_median_s": None}


@app.get("/runs")
def runs():
    out = []
    for p in sorted(RUNS.glob("frozen_*.json")):
        out.append(json.loads(p.read_text())["summary"])
    if out:
        return {"source": "live", "runs": out}
    summary, _ = read_json(FIXTURES / "summary.json")
    return {"source": "fixtures", "runs": [summary] if summary else []}


@app.get("/replay/{version}")
def replay(version: str, split: str = "frozen", day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    if not VERSION_RE.match(version) or not (POLICIES / f"{version}.py").exists():
        raise HTTPException(404, f"no policy {version}")
    if split not in SPLITS:
        raise HTTPException(400, f"split must be one of {sorted(SPLITS)}")
    results, src = None, "live"
    if split == "frozen":
        run, _ = read_json(RUNS / f"frozen_{version}.json")
        results = run["results"] if run else None
    if results is None:
        results = simulate_results(version, split)
    if results is None and version == "baseline":
        results, _ = read_json(FIXTURES / "results.json")
        src = "fixtures"
    if results is None:
        raise HTTPException(503, "simulator data missing; run backend/fetch.py")
    days = sorted({r["t"][:10] for r in results})
    if day:
        results = [r for r in results if r["t"].startswith(day)]
    results = sorted(results, key=lambda r: r["t"])
    return {"source": src, "version": version, "split": split, "days": days, "results": results}


@app.get("/posts")
def posts():
    data, path = read_json(DATA / "posts.json", FIXTURES / "posts.json")
    return {"source": source(path), "posts": data or []}


@app.get("/cameras")
def cameras():
    data, path = read_json(RUNS / "cameras.json", FIXTURES / "cameras.json")
    return {"source": source(path), "cameras": data or []}


@app.websocket("/live")
async def live(ws: WebSocket):
    """Pushes the run log and camera readings whenever either file changes."""
    await ws.accept()
    seen = {}
    try:
        while True:
            for name, fn, paths in [
                ("versions", versions, [RUNS / "versions.jsonl", FIXTURES / "versions.jsonl"]),
                ("cameras", cameras, [RUNS / "cameras.json", FIXTURES / "cameras.json"]),
            ]:
                stamp = tuple(p.stat().st_mtime if p.exists() else 0 for p in paths)
                if seen.get(name) != stamp:
                    seen[name] = stamp
                    await ws.send_json({"type": name, **fn()})
            await asyncio.sleep(LIVE_POLL_S)
    except WebSocketDisconnect:
        pass
