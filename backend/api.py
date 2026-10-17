"""FastAPI for the UI. Serves the agent's run log and replays from backend/runs/, falling back to fixtures/
when a run hasn't happened yet so the frontend works on a fresh clone.

    uvicorn api:app --reload --port 8000     (from backend/)
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
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
REPLAY_TIMEOUT_S = 180

log = logging.getLogger("golden-minute")
app = FastAPI(title="Golden Minute")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


def read_json(*paths):
    for p in paths:
        if p.exists():
            try:
                return json.loads(p.read_text()), p
            except json.JSONDecodeError:
                continue
    return None, None


def read_jsonl(*paths):
    for p in paths:
        if p.exists():
            rows = []
            for line in p.read_text().splitlines():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return rows, p
    return [], None


def source(path):
    return "fixtures" if path and FIXTURES in path.parents else "live"


def accepted_versions():
    rows, _ = read_jsonl(RUNS / "versions.jsonl")
    return {"baseline"} | {r["version"] for r in rows if r.get("accepted")}


def simulate_results(version, split):
    """Train/holdout replays run in a subprocess with a timeout and are cached; the frozen week never runs here."""
    cache = RUNS / f"replay_{version}_{split}.json"
    data, _ = read_json(cache)
    if data is not None:
        return data
    if not (DATA / "calls.parquet").exists() or not (DATA / "sf.graphml").exists():
        return None
    fd, name = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    out = Path(name)
    cmd = [sys.executable, str(BACKEND / "sim.py"), "--policy", version, "--split", split, "--out", str(out)]
    try:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC", "CLAUDE", "AWS_", "GH_", "GITHUB_"))}
        subprocess.run(cmd, check=True, capture_output=True, timeout=REPLAY_TIMEOUT_S, cwd=BACKEND, env=env)
        results = json.loads(out.read_text())["results"]
    except (subprocess.SubprocessError, json.JSONDecodeError, KeyError) as e:
        raise HTTPException(500, f"replay of {version} failed: {type(e).__name__}")
    finally:
        out.unlink(missing_ok=True)
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
    data, _ = read_json(RUNS / "headline.json")
    if data:
        return {"source": "live", **data}
    summary, _ = read_json(FIXTURES / "summary.json")
    return {"source": "fixtures", "version": None, "baseline_median_s": summary["median_s"] if summary else None, "agent_median_s": None}


@app.get("/runs")
def runs():
    out = []
    for p in sorted(RUNS.glob("frozen_*.json")):
        data, _ = read_json(p)
        if data:
            out.append(data["summary"])
    if out:
        return {"source": "live", "runs": out}
    summary, _ = read_json(FIXTURES / "summary.json")
    return {"source": "fixtures", "runs": [summary] if summary else []}


@app.get("/replay/{version}")
def replay(version: str, split: str = "frozen", day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    if not VERSION_RE.match(version) or not (POLICIES / f"{version}.py").exists():
        raise HTTPException(404, f"no policy {version}")
    if version not in accepted_versions():
        raise HTTPException(404, f"{version} was never accepted")
    if split not in SPLITS:
        raise HTTPException(400, f"split must be one of {sorted(SPLITS)}")
    src = "live"
    if split == "frozen":
        # the frozen week is only ever simulated by `agent.py --final`
        run, _ = read_json(RUNS / f"frozen_{version}.json")
        results = run["results"] if run else None
        if results is None and version == "baseline":
            results, _ = read_json(FIXTURES / "results.json")
            src = "fixtures"
        if results is None:
            raise HTTPException(404, f"no frozen-week run for {version}; run agent.py --final")
    else:
        results = simulate_results(version, split)
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
    feeds = [
        ("versions", versions, [RUNS / "versions.jsonl", FIXTURES / "versions.jsonl"]),
        ("cameras", cameras, [RUNS / "cameras.json", FIXTURES / "cameras.json"]),
    ]
    try:
        while True:
            for name, fn, paths in feeds:
                try:
                    stamp = tuple(p.stat().st_mtime if p.exists() else 0 for p in paths)
                    if seen.get(name) != stamp:
                        payload = {"type": name, **fn()}
                        seen[name] = stamp
                        await ws.send_json(payload)
                except (OSError, ValueError) as e:
                    log.warning("live %s: %s", name, e)
            await asyncio.sleep(LIVE_POLL_S)
    except WebSocketDisconnect:
        pass
