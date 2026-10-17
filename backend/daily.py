import argparse
import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parents[1]
BACKEND = ROOT / "backend"
DATA = ROOT / "data"
RUNS = ROOT / "runs"
FIXTURES = ROOT / "fixtures"
PY = sys.executable
CALIBRATION_TOLERANCE_PCT = 3.0
SHIFT_LIMIT_S = 15.0
BACKED_UP = ("*.parquet", "*.json")


def run(script, *args, timeout=3600):
    p = subprocess.run([PY, str(BACKEND / script), *args], capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f"{script} {' '.join(args)} failed: {(p.stderr or p.stdout).strip()[-400:]}")
    return p.stdout


def sim_json(*args):
    return json.loads(run("sim.py", *args))


def window():
    t = pd.read_parquet(DATA / "calls.parquet", columns=["t"])["t"]
    return t.min()[:10], t.max()[:10]


def backup(dest):
    dest.mkdir(parents=True, exist_ok=True)
    for pattern in BACKED_UP:
        for f in DATA.glob(pattern):
            shutil.copy2(f, dest / f.name)


def restore(src):
    for f in src.iterdir():
        shutil.copy2(f, DATA / f.name)


def calibration_error_pct():
    s = sim_json("--policy", "baseline", "--split", "fit")
    return round(100 * (s["median_s"] - s["actual_median_s"]) / s["actual_median_s"], 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--candidates", type=int, default=4)
    ap.add_argument("--accept-shift", action="store_true", help="publish even if the baseline moved more than the limit")
    ap.add_argument("--skip-fetch", action="store_true")
    a = ap.parse_args()

    today = date.today().isoformat()
    report = {"date": today, "window_before": window()}
    published = json.loads((FIXTURES / "engines_headline.json").read_text())
    slow_before = json.loads((DATA / "slow_nbhds.json").read_text())["nbhds"]

    if not a.skip_fetch:
        snap = RUNS / "backup" / today
        backup(snap)
        try:
            run("fetch.py", timeout=3600)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            restore(snap)
            report["error"] = f"fetch failed, data restored: {e}"
            return finish(report, 1)
    report["window_after"] = window()

    err = calibration_error_pct()
    report["calibration_error_pct"] = err
    report["recalibrated"] = abs(err) > CALIBRATION_TOLERANCE_PCT
    if report["recalibrated"]:
        run("sim.py", "--calibrate")
        report["calibration_error_pct_after"] = calibration_error_pct()
        slow_after = json.loads((DATA / "slow_nbhds.json").read_text())["nbhds"]
        if slow_after != slow_before:
            report["slow_nbhds_changed"] = {"before": slow_before, "after": slow_after}
    run("forecast.py")

    base = sim_json("--policy", "baseline", "--split", "frozen", "--engines")
    report["baseline_frozen_mean_s"] = {"published": published["baseline"]["mean_s"], "now": base["mean_s"]}
    shift = round(base["mean_s"] - published["baseline"]["mean_s"], 1)
    report["baseline_shift_s"] = shift

    run("agent.py", "--engines", "--rescore")
    rows = [json.loads(l) for l in (RUNS / "engines.jsonl").read_text().splitlines() if l.strip()]
    best = next(r["version"] for r in reversed(rows) if r["accepted"])
    hb = sim_json("--policy", best, "--split", "holdout", "--engines")
    bb = sim_json("--policy", "baseline", "--split", "holdout", "--engines")
    report["best_before"] = best
    report["best_still_beats_baseline_on_holdout"] = hb["weighted_mean_s"] < bb["weighted_mean_s"]

    if a.rounds:
        run("agent.py", "--engines", "--iters", str(a.rounds), "--candidates", str(a.candidates), timeout=6 * 3600)
    rows = [json.loads(l) for l in (RUNS / "engines.jsonl").read_text().splitlines() if l.strip()]
    report["best_after"] = next(r["version"] for r in reversed(rows) if r["accepted"])
    report["rounds"] = a.rounds

    if abs(shift) > SHIFT_LIMIT_S and not a.accept_shift:
        report["published"] = False
        report["held_back"] = f"baseline frozen-week average moved {shift:+.0f}s (limit {SHIFT_LIMIT_S:.0f}s); page left unchanged. Review, then rerun with --accept-shift --skip-fetch --rounds 0."
        return finish(report, 2)

    run("agent.py", "--engines", "--headline")
    report["published"] = True
    (FIXTURES / "learned.json").write_text(json.dumps({"last_learned": today, "window": report["window_after"], "best": report["best_after"], "rounds": a.rounds}, indent=1))
    return finish(report, 0)


def finish(report, code):
    RUNS.mkdir(exist_ok=True)
    (RUNS / f"daily_{report['date']}.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
