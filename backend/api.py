import json
import re
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).parents[1]
DATA = ROOT / "data"
RUNS = ROOT / "runs"
FIXTURES = ROOT / "fixtures"
POLICIES = Path(__file__).parent / "policies"
SPLITS = {"train", "holdout", "frozen"}

app = FastAPI(title="Golden Minute")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


def first(*paths):
    return next((p for p in paths if p.exists()), None)


def load_json(*paths):
    p = first(*paths)
    if not p:
        raise HTTPException(404, "not found")
    return json.loads(p.read_text())


def engines(mode):
    if mode not in ("", "engines"):
        raise HTTPException(404, "unknown mode")
    return mode == "engines"


def run(version, split, mode=""):
    eng = engines(mode)
    if not re.fullmatch(r"baseline|e\d{3}" if eng else r"baseline|v\d{3}", version) or split not in SPLITS or not (POLICIES / f"{version}.py").exists():
        raise HTTPException(404, "unknown version or split")
    out = RUNS / "results" / f"{version}_{split}{'_engines' if eng else ''}.json"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        p = subprocess.run([sys.executable, str(Path(__file__).parent / "sim.py"), "--policy", version, "--split", split, "--out", str(out), *(["--engines"] if eng else [])],
                           capture_output=True, text=True, timeout=180)
        if p.returncode:
            raise HTTPException(500, (p.stderr or p.stdout).strip()[-300:])
    return json.loads(out.read_text())


@app.get("/versions")
def versions(mode: str = ""):
    name = "engines" if engines(mode) else "versions"
    p = first(RUNS / f"{name}.jsonl", FIXTURES / f"{name if name == 'versions' else 'engines_versions'}.jsonl")
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


@app.get("/headline")
def headline(mode: str = ""):
    name = "engines_headline.json" if engines(mode) else "headline.json"
    return load_json(RUNS / name, FIXTURES / name)


@app.get("/lessons")
def lessons(mode: str = ""):
    p = first(RUNS / f"lessons_{'engines' if engines(mode) else 'ambulance'}.md", FIXTURES / ("engines_lessons.md" if engines(mode) else "lessons.md"))
    return [l[2:] for l in p.read_text().splitlines() if l.startswith("- ")] if p else []


@app.get("/learned")
def learned():
    return load_json(FIXTURES / "learned.json")


@app.get("/summary/{version}")
def summary(version: str, split: str = "frozen", mode: str = ""):
    return run(version, split, mode)["summary"]


@app.get("/results/{version}")
def results(version: str, split: str = "frozen", mode: str = ""):
    return run(version, split, mode)["results"]


@app.get("/policy/{version}")
def policy(version: str):
    if not re.fullmatch(r"baseline|[ev]\d{3}", version) or not (POLICIES / f"{version}.py").exists():
        raise HTTPException(404, "unknown version")
    return {"version": version, "code": (POLICIES / f"{version}.py").read_text()}


@app.get("/cameras")
def cameras():
    return load_json(DATA / "camera_readings.json", FIXTURES / "cameras.json")


@app.get("/cameras/{camera_id}/frame.jpg")
def camera_frame(camera_id: str):
    """The frame cameras.py analysed; its reading's frame.boxes are normalized to this image."""
    if not re.fullmatch(r"[A-Za-z0-9]{1,16}", camera_id):
        raise HTTPException(404, "unknown camera")
    path = first(DATA / "camera_frames" / f"{camera_id}.jpg", FIXTURES / "camera_frames" / f"{camera_id}.jpg")
    if not path:
        raise HTTPException(404, "no analysed frame")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@app.get("/posts")
def posts():
    return load_json(DATA / "posts.json", FIXTURES / "posts.json")


@app.get("/slow")
def slow():
    return load_json(DATA / "slow_nbhds.json")


@app.get("/speeds")
def speeds():
    return load_json(DATA / "speeds.json")


@app.get("/dispatch")
def dispatch(lat: float, lon: float, t: str, policy: str | None = None, priority: str = "3"):
    """One incident at a frozen-week moment: baseline vs agent pick, routes, and hospital. See dispatch.py."""
    if not (37.70 <= lat <= 37.84 and -122.53 <= lon <= -122.35):
        raise HTTPException(400, "location outside San Francisco")
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d)?", t):
        raise HTTPException(400, "t must look like 2026-10-06T17:40:00")
    if policy and (not re.fullmatch(r"baseline|[ev]\d{3}", policy) or not (POLICIES / f"{policy}.py").exists()):
        raise HTTPException(404, "unknown version")
    if priority not in ("2", "3"):
        raise HTTPException(400, "priority must be 2 or 3")
    import dispatch as live  # loads the road graph on first use only

    calls = live.frozen_calls()
    t = t if len(t) == 19 else t + ":00"
    if not calls["t"].min()[:10] <= t[:10] <= calls["t"].max()[:10]:
        raise HTTPException(400, "t must fall in the frozen week")
    return live.dispatch(lat, lon, t, policy, priority)
