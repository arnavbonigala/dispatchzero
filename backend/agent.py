import argparse
import json
import re
import subprocess
from datetime import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
BACKEND = ROOT / "backend"
POLICIES = BACKEND / "policies"
RUNS = ROOT / "runs"
FIXTURES = ROOT / "fixtures"
VERSIONS = RUNS / "versions.jsonl"
PY = sys.executable
HEADLINE = RUNS / "headline.json"
PREFIX = "v"
EXTRA = []
SIM_TIMEOUT_S = 120
LLM_TIMEOUT_S = 600
OVERALL_TOLERANCE_S = 5.0

GOAL = "Goal: make the 10 slowest neighborhoods faster (their median response time) without making the citywide median more than 5 s slower than baseline."
ENGINE_GOAL = "Goal: cut the citywide average time to the first paramedic (mean_s) by more than 60 s versus baseline, while keeping the 10 slowest neighborhoods faster than baseline. Each candidate must lower the training mean_s below the current best's."

MOVE_HINTS = """Big rewrites overfit the training weeks and slow the rest of the city. Prefer small, targeted changes to the current best.
A known working idea: move about 3 spare units from the biggest downtown posts to posts in the slow neighborhoods, leaving everyone else alone.
Moving 4 or more units looks free on training but slows everyone on busier weeks. Keep at most 3 units moved at once and at least 2 units at each downtown post. Look for gains in when units move (time buckets in post_plan) or in choose_unit, not in moving more units.

"""

SYSTEM = """You improve an ambulance dispatch policy for San Francisco, tested in a simulator that replays real 911 calls.

{goal}
Your candidate must beat the current best on the training weeks, then pass the same test on a holdout week you never see.
{hints}You control two functions in one Python file:

{contract}

{engines}Reply with exactly two sections:
<diagnosis>At most two short sentences, no caveats and no mention of testing: the specific problem you found in the data (name neighborhoods, hours, posts or units) and the change you made.</diagnosis>
<policy>
the complete new policy file, plain Python, no markdown fences
</policy>"""

PROMPT = """Slow neighborhoods: {slow}

Current best: {best}
Training summary of the current best:
{summary}

Baseline training summary:
{base}

Recent attempts (newest last):
{history}

{near}Current best policy file:
<current>
{code}
</current>

Worst training calls under the current best, with context. `avoidable_s` is controllable time beyond the drive from the closest post. `top_avoidable_nbhd_bucket_s` totals it per neighborhood and time bucket.
{worst}

Find one concrete improvement and write the new policy."""


ENGINE_NOTE = """Engine mode: the score is the time until the first paramedic is on scene, by ambulance or by fire engine. You may also define

def send_engine(call, engines, eta, ambulance_s) -> str | None

`engines` lists idle paramedic fire engines as {unit_id, post, kind: "engine"}; `eta(engine, call)` is that engine's seconds from dispatch to scene; `ambulance_s` is the chosen ambulance's seconds from dispatch to scene. Return an engine id to send it, or None. Injected globals: `ENGINES` (engine to its station post) and `ENGINE_LOAD` ("E01|15": that engine's own fire and other dispatches per day in that hour). Without send_engine, an engine goes only when history sent one. A sent engine is busy for about 10 minutes on scene, so it can miss its own fire calls: `stats.engine_conflicts` counts real engine calls that start while your policy has that engine busy, and a candidate with more than twice the baseline's conflicts is rejected.

"""


def sim(*args, timeout=SIM_TIMEOUT_S):
    p = subprocess.run([PY, str(BACKEND / "sim.py"), *args, *EXTRA], capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout).strip().splitlines()[-1])
    return json.loads(p.stdout)


def contract():
    readme = (ROOT / "README.md").read_text()
    return readme.split("## Policy contract", 1)[1].split("\n## ", 1)[0].strip()


def history():
    return [json.loads(line) for line in VERSIONS.read_text().splitlines() if line.strip()] if VERSIONS.exists() else []


def ask(prompt, system, model):
    p = subprocess.run(["claude", "-p", "--model", model, "--tools", "", "--no-session-persistence", "--system-prompt", system],
                       input=prompt, capture_output=True, text=True, timeout=LLM_TIMEOUT_S)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip())
    return p.stdout


def parse(reply):
    d = re.search(r"<diagnosis>(.*?)</diagnosis>", reply, re.S)
    c = re.search(r"<policy>(.*?)</policy>", reply, re.S)
    if not (d and c):
        raise ValueError("reply missing <diagnosis> or <policy>")
    code = re.sub(r"^```(?:python)?\n|```\s*$", "", c.group(1).strip()).strip()
    return d.group(1).strip(), code + "\n"


def evaluate(name, split):
    return sim("--policy", name, "--split", split, "--compare", "baseline")


def gates(train, best_train, holdout=None):
    for s in [train] + ([holdout] if holdout else []):
        if "engine_conflicts" in s["stats"]:
            base = baseline_conflicts(s["split"])
            if s["stats"]["engine_conflicts"] > 2 * base:
                return False, f"{s['split']}: {s['stats']['engine_conflicts']} engine conflicts vs baseline {base}"
    if EXTRA:
        if train["mean_s"] >= best_train["mean_s"]:
            return False, f"train: average {train['mean_s']:.0f}s not below best {best_train['mean_s']:.0f}s"
        if holdout is not None and holdout["mean_s"] >= baseline_summary("holdout")["mean_s"]:
            return False, f"holdout: average {holdout['mean_s']:.0f}s not below baseline {baseline_summary('holdout')['mean_s']:.0f}s"
    elif train["slow_median_s"] >= best_train["slow_median_s"]:
        return False, f"train: slow median {train['slow_median_s']:.0f}s not below best {best_train['slow_median_s']:.0f}s"
    if train["vs_baseline"]["everyone"]["median_diff_s"] > OVERALL_TOLERANCE_S:
        return False, f"train: citywide {train['vs_baseline']['everyone']['median_diff_s']:+.0f}s vs baseline"
    if holdout is not None and not holdout["vs_baseline"]["passes"]:
        g = holdout["vs_baseline"]
        return False, f"holdout: slow {g['slow_nbhds']['median_diff_s']:+.0f}s CI [{g['slow_nbhds']['ci95'][0]:.0f}, {g['slow_nbhds']['ci95'][1]:.0f}], citywide {g['everyone']['median_diff_s']:+.0f}s"
    return True, "passed train and holdout"


def baseline_summary(split, cache={}):
    if split not in cache:
        cache[split] = sim("--policy", "baseline", "--split", split)
    return cache[split]


def baseline_conflicts(split):
    return baseline_summary(split)["stats"]["engine_conflicts"]


def record(row):
    RUNS.mkdir(exist_ok=True)
    with VERSIONS.open("a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps(row), flush=True)


def next_name():
    nums = [int(p.stem[1:]) for p in POLICIES.glob(f"{PREFIX}[0-9][0-9][0-9].py")]
    return f"{PREFIX}{max(nums, default=0) + 1:03d}"


def seed():
    train = evaluate("baseline", "train")
    record({"version": "baseline", "parent": "", "diagnosis": "Every unit waits at its home post; the closest idle unit is sent.",
            "train_median": train["median_s"], "frozen_median": None, "accepted": True,
            "train_slow_median": train["slow_median_s"], "holdout_slow_diff": 0.0, "holdout_all_diff": 0.0, "reason": "reference"})


def step(model):
    rows = history()
    best = next(r["version"] for r in reversed(rows) if r["accepted"])
    best_train = evaluate(best, "train")
    base_train = evaluate("baseline", "train")
    worst = sim("--policy", best, "--split", "train", "--worst", "50")
    slim = lambda s: {k: s[k] for k in ("slow_median_s", "slow_n", "median_s", "mean_s", "p90_s", "n", "stats") if k in s}
    recent = [{**{k: r.get(k) for k in ("version", "parent", "diagnosis", "accepted", "train_slow_median", "train_all_diff", "train_mean")},
               "reason": "failed the holdout gate" if str(r.get("reason", "")).startswith("holdout") else r.get("reason")} for r in rows[-5:]]
    near = [r for r in rows if str(r.get("reason", "")).startswith("holdout") and (POLICIES / f"{r['version']}.py").exists()]
    near = min(near, key=lambda r: r["train_slow_median"], default=None)
    near = (f"Closest rejected candidate ({near['version']}): it beat the current best on training but failed the holdout gate. "
            f"Consider a more conservative version of it.\n<near_miss>\n{(POLICIES / (near['version'] + '.py')).read_text()}</near_miss>\n\n") if near else ""
    prompt = PROMPT.format(
        slow=", ".join(json.loads((ROOT / "data" / "slow_nbhds.json").read_text())["nbhds"]),
        best=best, summary=json.dumps(slim(best_train)), base=json.dumps(slim(base_train)),
        history="\n".join(json.dumps(r) for r in recent), near=near, code=(POLICIES / f"{best}.py").read_text(),
        worst=json.dumps({k: v for k, v in worst.items() if k != "summary"}))
    name = next_name()
    row = {"version": name, "parent": best, "diagnosis": "", "train_median": None, "frozen_median": None, "accepted": False}
    try:
        row["diagnosis"], code = parse(ask(prompt, SYSTEM.format(contract=contract(), engines=ENGINE_NOTE if EXTRA else "", hints="" if EXTRA else MOVE_HINTS, goal=ENGINE_GOAL if EXTRA else GOAL), model))
        (POLICIES / f"{name}.py").write_text(code)
        train = evaluate(name, "train")
        row |= {"train_median": train["median_s"], "train_slow_median": train["slow_median_s"], "train_all_diff": train["vs_baseline"]["everyone"]["median_diff_s"], "train_mean": train["mean_s"]}
        ok, why = gates(train, best_train)
        if ok:
            holdout = evaluate(name, "holdout")
            g = holdout["vs_baseline"]
            row |= {"holdout_slow_diff": g["slow_nbhds"]["median_diff_s"], "holdout_slow_ci": g["slow_nbhds"]["ci95"], "holdout_all_diff": g["everyone"]["median_diff_s"],
                    "holdout_mean_diff": round(holdout["mean_s"] - baseline_summary("holdout")["mean_s"], 1)}
            ok, why = gates(train, best_train, holdout)
        if ok:
            row["frozen_median"] = sim("--policy", name, "--split", "frozen")["median_s"]
        row |= {"accepted": ok, "reason": why}
    except (RuntimeError, ValueError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        row["reason"] = f"rejected: {str(e)[:300]}"
    record(row)


def rescore():
    rows = history()
    for r in rows:
        if not (POLICIES / f"{r['version']}.py").exists():
            continue
        try:
            train = evaluate(r["version"], "train")
            holdout = evaluate(r["version"], "holdout")
        except RuntimeError:
            continue
        g = holdout["vs_baseline"]
        r |= {"train_median": train["median_s"], "train_slow_median": train["slow_median_s"], "train_all_diff": train["vs_baseline"]["everyone"]["median_diff_s"], "train_mean": train["mean_s"],
              "holdout_slow_diff": g["slow_nbhds"]["median_diff_s"], "holdout_slow_ci": g["slow_nbhds"]["ci95"], "holdout_all_diff": g["everyone"]["median_diff_s"],
              "holdout_passes": g["passes"] or r["version"] == "baseline"}
        if r["accepted"]:
            r["frozen_median"] = sim("--policy", r["version"], "--split", "frozen")["median_s"]
        print(r["version"], r["accepted"], r["holdout_passes"], round(r["holdout_slow_diff"]), round(r["holdout_all_diff"], 1), flush=True)
    VERSIONS.write_text("".join(json.dumps(r) + "\n" for r in rows))


def headline():
    best = next(r["version"] for r in reversed(history()) if r["accepted"])
    s = evaluate(best, "frozen")
    b = sim("--policy", "baseline", "--split", "frozen")
    keys = ("slow_median_s", "median_s", "mean_s", "p90_s", "actual_median_s", "n")
    out = {"version": best, "frozen": {k: s[k] for k in keys}, "baseline": {k: b[k] for k in keys}, "vs_baseline": s["vs_baseline"]}
    RUNS.mkdir(exist_ok=True)
    HEADLINE.write_text(json.dumps(out, indent=1))
    if EXTRA:
        snapshot(best)
    print(json.dumps(out, indent=1))


def snapshot(best):
    for name, version in (("engines_baseline", "baseline"), ("engines_agent", best)):
        tmp = RUNS / f"{name}.json"
        sim("--policy", version, "--split", "frozen", "--out", str(tmp))
        d = json.loads(tmp.read_text())
        tuesday = [r for r in d["results"] if datetime.fromisoformat(r["t"]).weekday() == 1]
        (FIXTURES / f"{name}.json").write_text(json.dumps({"version": version, "summary": d["summary"], "results": tuesday}))
    (FIXTURES / "engines_headline.json").write_text(HEADLINE.read_text())
    (FIXTURES / "engines_versions.jsonl").write_text(VERSIONS.read_text())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=1)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--headline", action="store_true")
    ap.add_argument("--rescore", action="store_true")
    ap.add_argument("--engines", action="store_true")
    a = ap.parse_args()
    if a.engines:
        global VERSIONS, HEADLINE, PREFIX, EXTRA
        VERSIONS, HEADLINE, PREFIX, EXTRA = RUNS / "engines.jsonl", RUNS / "engines_headline.json", "e", ["--engines"]
    if not history():
        seed()
    if a.rescore:
        return rescore()
    if a.headline:
        return headline()
    for _ in range(a.iters):
        step(a.model)


if __name__ == "__main__":
    main()
