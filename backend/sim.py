import argparse
import json
import statistics
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parents[1]
DATA = ROOT / "data"
FIXTURES = ROOT / "fixtures"


def load_calls(split=None):
    calls = pd.read_parquet(DATA / "calls.parquet")
    if split:
        calls = calls[calls["split"] == split]
    return calls.to_dict("records")


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


def replay(calls, policy=None, slowdowns=None, version="stub", split="train"):
    units = json.loads((DATA / "units.json").read_text())
    results = [
        {
            "call_id": c["call_id"],
            "t": c["t"],
            "lat": c["lat"],
            "lon": c["lon"],
            "unit": c["historical_unit"],
            "from_post": units.get(c["historical_unit"], -1),
            "eta_s": c["drive_s"],
            "response_s": c["processing_s"] + c["turnout_s"] + c["drive_s"],
            "historical_s": c["historical_s"],
            "hour": c["hour"],
            "nbhd": c["nbhd"],
        }
        for c in calls
    ]
    return results, summarize(results, version, split)


def write_fixtures():
    FIXTURES.mkdir(exist_ok=True)
    calls = load_calls("frozen")
    tuesday = [c for c in calls if pd.Timestamp(c["t"]).dayofweek == 1]
    results, summary = replay(tuesday, version="baseline", split="frozen")
    (FIXTURES / "results.json").write_text(json.dumps(results, indent=1))
    (FIXTURES / "summary.json").write_text(json.dumps(summary, indent=1))
    versions = [
        {"version": "baseline", "parent": "", "diagnosis": "Closest idle unit from each unit's home station.", "train_median": 612.0, "frozen_median": 618.0, "accepted": True},
        {"version": "v001", "parent": "baseline", "diagnosis": "Bayview had no idle unit 17:00-19:00 on weekdays; repost one Mission unit to station area 17 in that window.", "train_median": 596.0, "frozen_median": 604.0, "accepted": True},
        {"version": "v002", "parent": "v001", "diagnosis": "Hold a reserve unit downtown overnight.", "train_median": 589.0, "frozen_median": 607.0, "accepted": False},
        {"version": "v003", "parent": "v001", "diagnosis": "Tenderloin calls cluster 10:00-14:00; shift two units to station area 3 midday.", "train_median": 581.0, "frozen_median": 590.0, "accepted": True},
    ]
    (FIXTURES / "versions.jsonl").write_text("".join(json.dumps(v) + "\n" for v in versions))
    cams = json.loads((DATA / "cameras.json").read_text())[:10]
    readings = [
        {"camera_id": c["camera_id"], "name": c["name"], "lat": c["lat"], "lon": c["lon"], "ts": "2026-10-17T17:30:00",
         "vehicles": 8 + 3 * i, "moving_frac": 0.2 if i == 2 else 0.9, "slowdown": 3.2 if i == 2 else 1.1}
        for i, c in enumerate(cams)
    ]
    (FIXTURES / "cameras.json").write_text(json.dumps(readings, indent=1))
    (FIXTURES / "posts.json").write_text((DATA / "posts.json").read_text())
    print(f"fixtures: {len(results)} results, summary {summary['median_s']:.0f}s median, {len(versions)} versions, {len(readings)} cameras")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="train")
    ap.add_argument("--fixtures", action="store_true")
    a = ap.parse_args()
    if a.fixtures:
        write_fixtures()
    else:
        print(json.dumps(replay(load_calls(a.split), split=a.split)[1], indent=1))
