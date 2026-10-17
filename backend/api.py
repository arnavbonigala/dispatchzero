import json
import re
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
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


def run(version, split):
    if not re.fullmatch(r"baseline|v\d{3}", version) or split not in SPLITS or not (POLICIES / f"{version}.py").exists():
        raise HTTPException(404, "unknown version or split")
    out = RUNS / "results" / f"{version}_{split}.json"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        p = subprocess.run([sys.executable, str(Path(__file__).parent / "sim.py"), "--policy", version, "--split", split, "--out", str(out)],
                           capture_output=True, text=True, timeout=180)
        if p.returncode:
            raise HTTPException(500, (p.stderr or p.stdout).strip()[-300:])
    return json.loads(out.read_text())


@app.get("/versions")
def versions():
    p = first(RUNS / "versions.jsonl", FIXTURES / "versions.jsonl")
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


@app.get("/headline")
def headline():
    return load_json(RUNS / "headline.json", FIXTURES / "headline.json")


@app.get("/summary/{version}")
def summary(version: str, split: str = "frozen"):
    return run(version, split)["summary"]


@app.get("/results/{version}")
def results(version: str, split: str = "frozen"):
    return run(version, split)["results"]


@app.get("/policy/{version}")
def policy(version: str):
    if not re.fullmatch(r"baseline|v\d{3}", version) or not (POLICIES / f"{version}.py").exists():
        raise HTTPException(404, "unknown version")
    return {"version": version, "code": (POLICIES / f"{version}.py").read_text()}


@app.get("/cameras")
def cameras():
    return load_json(DATA / "camera_readings.json", FIXTURES / "cameras.json")


@app.get("/posts")
def posts():
    return load_json(DATA / "posts.json", FIXTURES / "posts.json")


@app.get("/slow")
def slow():
    return load_json(DATA / "slow_nbhds.json")


@app.get("/speeds")
def speeds():
    return load_json(DATA / "speeds.json")
