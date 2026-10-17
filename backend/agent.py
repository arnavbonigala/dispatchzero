"""Improvement loop: Claude rewrites the dispatch policy to speed up SF's slowest neighborhoods.

A candidate is promoted only if, against a fresh run of the current best and the baseline in the same simulator:
  gate 1 (training weeks): it lowers the slow-neighborhood median by at least MIN_GAIN_S and keeps the citywide
          median within sim.OVERALL_TOLERANCE_S of the baseline;
  gate 2 (holdout week): it lowers the slow-neighborhood median again and sim.goal(...) vs baseline passes
          (slow-neighborhood 95% CI below zero, citywide at most 5 s slower).
The frozen week is never shown to the agent; `--final` evaluates it once for the headline."""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).parent
POLICIES = BACKEND / "policies"
RUNS = BACKEND / "runs"
VERSIONS = RUNS / "versions.jsonl"
HEADLINE = RUNS / "headline.json"
MODEL = "claude-sonnet-5-5"
EVAL_TIMEOUT_S = 600
LLM_TIMEOUT_S = 1200
MAX_INVALID_RATE = 0.02
MIN_GAIN_S = 2.0
WORST_N = 50
HISTORY_N = 5
POLICY_SEED = 0

SYSTEM = """You are the policy engineer for Golden Minute, an ambulance dispatcher for San Francisco.
You write a Python dispatch policy that a simulator replays against real 911 medical calls.

THE GOAL: make the city's slowest neighborhoods (listed in the prompt) get an ambulance sooner, measured by the median
response time (call received to ambulance on scene) of calls in those neighborhoods, without making the citywide
median more than 5 seconds slower than the baseline. You control only two things: where each unit waits at each hour,
and which idle unit is sent to each call. Processing and turnout delays are fixed.

The policy file must define exactly these two functions:

    def post_plan(hour: int, units: list[str]) -> dict[str, int]   # unit_id -> post index
    def choose_unit(call: dict, idle: list[dict], eta) -> str        # returns one idle unit_id

Facts about the simulator:
- `post_plan(hour, units)` is called at every hour boundary with the units on shift at that moment. Units it leaves
  out keep their current post. A unit sent to a new post drives there (its drive time between posts); it stays
  available while moving but is dispatched from its old post until it arrives.
- After each call the unit drives back to its current post before it is available again.
- `call` has only: call_id (opaque), t (ISO local time string like "2026-10-06T17:04:17"), lat, lon, hour, nbhd,
  station_area, priority ("1", "2", "3" or "E"; lower number is more urgent).
- `idle` is a list of {"unit_id": str, "post": int}. `eta(unit, call)` returns drive seconds from that unit's current
  position to the call.
- If no unit is idle the call queues for the first unit to free up, regardless of the policy.
- Injected globals, available at module level: HOME (unit_id -> home post, the baseline layout, spread by call
  volume), POSTS (list of {"post", "station_area", "lat", "lon"}; post i is POSTS[i]), DEMAND
  ({bucket: {post: training calls}}, each call counted at its nearest post), POST_DRIVE (POST_DRIVE[a][b] = drive
  seconds from post a to post b) and BUCKET_OF (hour -> "night" | "morning_rush" | "midday" | "evening_rush" |
  "evening").
- Allowed imports: math, statistics, collections, itertools, functools, heapq, bisect, random. Use a module only as
  module.function (e.g. math.sqrt); never pass a module around. No file access, no eval/exec, no getattr, no names or
  attributes starting with an underscore. Violations reject the policy.
- If choose_unit raises or returns something that is not an idle unit_id, the simulator sends the closest idle unit
  and counts an error. A policy with errors on more than 2% of calls, or whose post_plan raises, is rejected.
  Keep the code fast and deterministic.
- The policy cannot read data at runtime beyond the globals above, so any other per-area or per-hour knowledge must be
  written into the file as constants.

Every change is tested on a week of calls you have not seen. Prefer changes that generalize (moving spare capacity
toward under-served neighborhoods at the hours they are under-served, keeping a reserve where demand is high) over
fitting individual calls. Pulling too many units out of busy areas slows the rest of the city and fails the goal.

Reply in exactly this format:
<diagnosis>One to three plain sentences a dispatcher could read aloud: what was slow, where and when, and what the
new policy changes. Name neighborhoods, hours and units.</diagnosis>
```python
# the complete new policy file
```"""


def load_versions():
    if not VERSIONS.exists():
        return []
    rows = []
    for line in VERSIONS.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def append_version(row):
    rows = load_versions() + [row]
    write_atomic(VERSIONS, "".join(json.dumps(r) + "\n" for r in rows))


def best_version(rows):
    accepted = [r for r in rows if r["accepted"]]
    return accepted[-1] if accepted else None


def next_name(rows):
    nums = [int(r["version"][1:]) for r in rows if re.fullmatch(r"v\d+", r["version"])]
    nums += [int(p.stem[1:]) for p in POLICIES.glob("v[0-9][0-9][0-9].py")]
    return f"v{max(nums, default=0) + 1:03d}"


def policy_path(version):
    return POLICIES / f"{version}.py"


# ---------------------------------------------------------------- evaluation (runs in a subprocess)


def evaluate(policy, splits, with_diagnostics=False):
    """Simulate the policy and the baseline on each split. Only training data is returned in detail."""
    import random

    import sim

    world = sim.World.load()
    mod = sim.load_policy(policy, world)
    base = sim.load_policy("baseline", world)
    post_plan_errors = 0
    for h in range(24):
        try:
            dict(mod.post_plan(h, list(world.units)))
        except Exception:
            post_plan_errors += 1
    out = {"post_plan_errors": post_plan_errors}
    for split in splits:
        calls = sim.load_calls(split)
        random.seed(POLICY_SEED)
        results, stats = sim.simulate(world, calls, mod)
        base_results, _ = sim.simulate(world, calls, base)
        entry = {
            "summary": sim.summarize(results, Path(str(policy)).stem, split),
            "baseline": sim.summarize(base_results, "baseline", split),
            "stats": stats,
            "vs_baseline": sim.goal(results, base_results),
        }
        if split == "train" and with_diagnostics:
            random.seed(POLICY_SEED)
            entry["diagnostics"] = diagnostics(world, calls, results, mod)
        out[split] = entry
    return out


def diagnostics(world, calls, results, mod):
    import sim

    slow = set(sim.load_slow())
    area_of = {c["call_id"]: c["station_area"] for c in calls}

    def table(key, rows=results):
        groups = defaultdict(list)
        for r in rows:
            groups[key(r)].append(r)
        return {
            k: {
                "n": len(g),
                "median_s": round(statistics.median(x["response_s"] for x in g)),
                "median_drive_s": round(statistics.median(x["eta_s"] for x in g)),
                "pct_waited": round(sum(x["wait_s"] > 0 for x in g) / len(g), 3),
            }
            for k, g in sorted(groups.items())
        }

    demand = defaultdict(lambda: defaultdict(int))
    for c in calls:
        demand[c["station_area"]][sim.BUCKET_OF[c["hour"]]] += 1
    days = len({c["t"][:10] for c in calls}) or 1
    worst = sim.worst_calls(world, calls, mod, WORST_N)
    slow_rows = [r for r in results if r["nbhd"] in slow]

    return {
        "slow_nbhds": sorted(slow),
        "worst": worst["worst"],
        "worst_by_nbhd": worst["worst_by_nbhd"],
        "worst_by_bucket": worst["worst_by_bucket"],
        "queued_in_worst": worst["queued_in_worst"],
        "top_avoidable_nbhd_bucket_s": worst["top_avoidable_nbhd_bucket_s"],
        "slow_by_nbhd_bucket": table(lambda r: f"{r['nbhd']}|{sim.BUCKET_OF[r['hour']]}", slow_rows),
        "by_nbhd": table(lambda r: r["nbhd"]),
        "by_hour": table(lambda r: r["hour"]),
        "by_area": table(lambda r: area_of[r["call_id"]]),
        "demand_per_day": {a: {b: round(n / days, 2) for b, n in d.items()} for a, d in sorted(demand.items())},
        "on_duty_by_hour": on_duty_by_hour(world, calls),
    }


def on_duty_by_hour(world, calls):
    import sim

    days = sorted({c["t"][:10] for c in calls})
    out = {}
    for h in range(24):
        counts = []
        for d in days:
            t = int(sim.epoch([f"{d}T{h:02d}:30:00"])[0])
            counts.append(sum(sim.on_duty(world.windows, u, t) for u in world.units))
        out[h] = round(statistics.mean(counts), 1)
    return out


def eval_env():
    """Candidate code never needs credentials; keep them out of its process."""
    return {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC", "CLAUDE", "AWS_", "GH_", "GITHUB_"))}


def run_eval(policy, splits, with_diagnostics=False):
    """Run the candidate in a subprocess with a timeout so a hung or crashing policy can't take the loop down."""
    fd, name = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    out = Path(name)
    cmd = [sys.executable, str(Path(__file__)), "--eval", str(policy), "--splits", ",".join(splits), "--out", str(out)]
    if with_diagnostics:
        cmd.append("--diagnostics")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=EVAL_TIMEOUT_S, cwd=BACKEND, env=eval_env())
        if p.returncode != 0:
            return None, (p.stderr.strip().splitlines() or ["crashed"])[-1][:300]
        return json.loads(out.read_text()), None
    except subprocess.TimeoutExpired:
        return None, f"timed out after {EVAL_TIMEOUT_S}s"
    finally:
        out.unlink(missing_ok=True)


# ---------------------------------------------------------------- prompting


def build_prompt(code, ev, history, world_info):
    train, d = ev["train"], ev["train"]["diagnostics"]
    s, b = train["summary"], train["baseline"]
    lines = [
        "## The slowest neighborhoods (the goal)",
        json.dumps(d["slow_nbhds"]),
        "",
        "## Current best policy",
        "```python",
        code.strip(),
        "```",
        "",
        "## Its result on the training weeks, next to the baseline",
        json.dumps({
            "slow_median_s": s["slow_median_s"], "slow_n": s["slow_n"], "median_s": s["median_s"], "p90_s": s["p90_s"],
            "baseline_slow_median_s": b["slow_median_s"], "baseline_median_s": b["median_s"],
            "citywide_budget_left_s": round(b["median_s"] + 5 - s["median_s"], 1), "stats": train["stats"],
        }),
        "",
        "## Recent attempts (newest last; the unseen-week test is pass/fail only)",
    ]
    for h in history:
        verdict = "accepted" if h["accepted"] else f"rejected ({h.get('reason') or 'failed a gate'})"
        lines.append(f"- {h['version']} (parent {h['parent'] or '-'}), train slow median {h.get('train_slow_median')}s, citywide {h.get('train_median')}s, {verdict}: {h['diagnosis']}")
    lines += [
        "",
        "## Slow neighborhoods by time bucket (training weeks)",
        json.dumps(d["slow_by_nbhd_bucket"]),
        "",
        f"## The {WORST_N} training calls with the most controllable time (queue wait + drive) under the current policy",
        "avoidable_s is how much of that a unit idle at the closest post would have saved. units_at_closest_post says",
        "which units were based at that post and whether they were idle, busy or off duty at dispatch.",
        *(json.dumps(w) for w in d["worst"]),
        "",
        "## Where the worst calls cluster",
        json.dumps({"by_nbhd": d["worst_by_nbhd"], "by_bucket": d["worst_by_bucket"], "queued_in_worst": d["queued_in_worst"]}),
        "",
        "## Top (neighborhood|bucket) cells by total avoidable seconds over all training calls",
        json.dumps(d["top_avoidable_nbhd_bucket_s"]),
        "",
        "## Response by neighborhood (training weeks)",
        json.dumps(d["by_nbhd"]),
        "",
        "## Response by hour of day (training weeks)",
        json.dumps(d["by_hour"]),
        "",
        "## Response by station area (training weeks)",
        json.dumps(d["by_area"]),
        "",
        "## Calls per day by station area and time bucket (night 0-6, morning_rush 7-9, midday 10-15, evening_rush 16-18, evening 19-23)",
        json.dumps(d["demand_per_day"]),
        "",
        "## Average units on shift by hour",
        json.dumps(d["on_duty_by_hour"]),
        "",
        "## POSTS (index, station_area, lat, lon)",
        json.dumps([[p["post"], p["station_area"], round(p["lat"], 4), round(p["lon"], 4)] for p in world_info["posts"]]),
        "",
        "## HOME (unit -> home post)",
        json.dumps(world_info["homes"]),
        "",
        "Diagnose the biggest fixable cause of slow responses in the slowest neighborhoods, then write the complete improved policy file.",
    ]
    return "\n".join(lines)


def ask_claude(prompt, backend, stub_iter=0):
    if backend == "stub":
        return stub_reply(stub_iter)
    if backend == "api":
        import anthropic

        client = anthropic.Anthropic()
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=64000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            raise RuntimeError("model declined the request")
        if msg.stop_reason == "max_tokens":
            raise RuntimeError("reply hit max_tokens")
        return "".join(b.text for b in msg.content if b.type == "text")
    with tempfile.TemporaryDirectory() as cwd:
        cmd = ["claude", "-p", "--model", MODEL, "--tools", "", "--setting-sources", "", "--strict-mcp-config",
               "--no-session-persistence", "--system-prompt", SYSTEM, "--output-format", "json"]
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=LLM_TIMEOUT_S, cwd=cwd)
    try:
        reply = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude cli failed: {(p.stderr or p.stdout).strip()[:300]}")
    if reply.get("is_error"):
        raise RuntimeError(f"claude cli error: {str(reply.get('result'))[:300]}")
    return reply["result"]


def stub_reply(i):
    """Offline stand-in for Claude, used to test the loop without spending tokens."""
    k = [1, 2, 3, 4][i % 4]
    return f"""<diagnosis>Stub {i}: move the {k} units whose home post has the most units to the posts with the most training calls per unit.</diagnosis>
```python
from collections import Counter

def post_plan(hour, units):
    load = Counter(HOME[u] for u in units)
    calls = DEMAND.get(BUCKET_OF[hour], {{}})
    plan = {{}}
    spare = sorted(units, key=lambda u: -load[HOME[u]])[:{k}]
    targets = sorted(range(len(POSTS)), key=lambda p: -calls.get(p, calls.get(str(p), 0)) / (1 + load[p]))
    for u, p in zip(spare, targets):
        plan[u] = p
    return plan


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]
```"""


def parse_reply(text):
    m = re.search(r"<diagnosis>(.*?)</diagnosis>", text, re.S)
    blocks = re.findall(r"```python\n(.*?)```", text, re.S)
    if not m or not blocks:
        raise ValueError("reply missing <diagnosis> or a ```python block")
    return " ".join(m.group(1).split()), max(blocks, key=len).strip() + "\n"


def check_policy(code):
    import sim

    compile(code, "<policy>", "exec")
    sim.check_source(code)
    missing = {"post_plan", "choose_unit"} - set(re.findall(r"^def (\w+)\(", code, re.M))
    if missing:
        raise ValueError(f"policy is missing {sorted(missing)}")


# ---------------------------------------------------------------- loop


def world_info():
    data = BACKEND.parent / "data"
    return {"posts": json.loads((data / "posts.json").read_text()), "homes": json.loads((data / "units.json").read_text())}


def ensure_baseline(rows):
    if rows:
        return rows
    print("evaluating baseline")
    ev, err = run_eval(policy_path("baseline"), ["train", "holdout"])
    if err:
        raise SystemExit(f"baseline failed: {err}")
    row = version_row("baseline", "", "Each unit waits at its home post (spread by call volume); the closest idle unit is sent.", ev, True, None)
    append_version(row)
    return [row]


def version_row(version, parent, diagnosis, ev, accepted, reason, model=None):
    def pick(split, key):
        v = ev[split]["summary"][key] if ev and split in ev else None
        return round(v, 1) if isinstance(v, float) else v

    hold_goal = ev["holdout"]["vs_baseline"] if ev and "holdout" in ev else None
    return {
        "version": version,
        "parent": parent,
        "diagnosis": diagnosis,
        "train_median": pick("train", "median_s"),
        "holdout_median": pick("holdout", "median_s"),
        "frozen_median": None,
        "train_slow_median": pick("train", "slow_median_s"),
        "holdout_slow_median": pick("holdout", "slow_median_s"),
        "frozen_slow_median": None,
        "holdout_passes": hold_goal["passes"] if hold_goal else None,
        "accepted": accepted,
        "reason": reason,
        "train_p90": pick("train", "p90_s"),
        "stats": ev["train"]["stats"] if ev and "train" in ev else None,
        "model": model,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }


def gate(ev, ev_best):
    """Returns None if the candidate is promoted, else the reason it was rejected."""
    import sim

    tr, ho = ev["train"], ev["holdout"]
    if ev["post_plan_errors"]:
        return f"post_plan raised for {ev['post_plan_errors']} of 24 hours"
    bad = tr["stats"]["invalid_choice"] / tr["summary"]["n"]
    if bad > MAX_INVALID_RATE:
        return f"choose_unit failed on {bad:.1%} of calls"
    if tr["summary"]["slow_median_s"] > ev_best["train"]["summary"]["slow_median_s"] - MIN_GAIN_S:
        return "failed train gate: slow neighborhoods not faster"
    if tr["summary"]["median_s"] > tr["baseline"]["median_s"] + sim.OVERALL_TOLERANCE_S:
        return "failed train gate: citywide median too slow"
    if ho["summary"]["slow_median_s"] >= ev_best["holdout"]["summary"]["slow_median_s"]:
        return "failed holdout gate: slow neighborhoods not faster"
    if not ho["vs_baseline"]["passes"]:
        return "failed holdout gate: goal not met vs baseline"
    return None


def iterate(backend, i):
    rows = ensure_baseline(load_versions())
    best = best_version(rows)
    code = policy_path(best["version"]).read_text()
    ev_best, err = run_eval(policy_path(best["version"]), ["train", "holdout"], with_diagnostics=True)
    if err:
        raise SystemExit(f"current best {best['version']} failed to evaluate: {err}")
    prompt = build_prompt(code, ev_best, rows[-HISTORY_N:], world_info())
    name = next_name(rows)
    model = "stub" if backend == "stub" else MODEL
    (RUNS / "llm").mkdir(parents=True, exist_ok=True)
    (RUNS / "llm" / f"{name}.prompt.md").write_text(prompt)

    diagnosis, new_code, reason = "", None, None
    for attempt in range(2):
        reply = None
        try:
            reply = ask_claude(prompt, backend, i)
            (RUNS / "llm" / f"{name}.reply.md").write_text(reply)
            diagnosis, new_code = parse_reply(reply)
            check_policy(new_code)
            reason = None
            break
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"[:300]
            new_code = None
            print(f"{name}: attempt {attempt + 1} unusable ({reason})")
            if reply is None:
                break
            prompt += f"\n\nYour previous reply could not be used: {reason}\nReply again in the required format with a complete, valid policy file."
    if new_code is None:
        row = version_row(name, best["version"], diagnosis or "(no usable reply)", None, False, reason, model)
        append_version(row)
        return row

    path = policy_path(name)
    path.write_text(new_code)
    ev, err = run_eval(path, ["train", "holdout"])
    if err:
        row = version_row(name, best["version"], diagnosis, None, False, f"crashed: {err}", model)
    else:
        reason = gate(ev, ev_best)
        row = version_row(name, best["version"], diagnosis, ev, reason is None, reason, model)
        row["parent_train_slow_median"] = round(ev_best["train"]["summary"]["slow_median_s"], 1)
    append_version(row)
    verdict = "ACCEPTED" if row["accepted"] else f"rejected: {row['reason']}"
    print(f"{name} (parent {best['version']}): train slow {row['train_slow_median']} vs {round(ev_best['train']['summary']['slow_median_s'], 1)}, "
          f"citywide {row['train_median']}, {verdict}\n  {diagnosis}")
    return row


def final(force=False):
    """Touch the frozen week once: best accepted version vs baseline, with paired bootstrap CIs."""
    if HEADLINE.exists() and not force:
        raise SystemExit(f"{HEADLINE} exists: the frozen week was already evaluated. Pass --force to evaluate it again.")
    rows = load_versions()
    best = best_version(rows)
    if not best or best["version"] == "baseline":
        raise SystemExit("no accepted version beyond baseline yet")
    out = {}
    for v in ["baseline", best["version"]]:
        f = RUNS / f"frozen_{v}.json"
        cmd = [sys.executable, str(BACKEND / "sim.py"), "--policy", v, "--split", "frozen", "--out", str(f)]
        if v != "baseline":
            cmd += ["--compare", "baseline"]
        subprocess.run(cmd, check=True, capture_output=True, timeout=EVAL_TIMEOUT_S, cwd=BACKEND, env=eval_env())
        out[v] = json.loads(f.read_text())["summary"]
    for r in rows:
        if r["version"] in out:
            r["frozen_median"] = round(out[r["version"]]["median_s"], 1)
            r["frozen_slow_median"] = round(out[r["version"]]["slow_median_s"], 1)
    write_atomic(VERSIONS, "".join(json.dumps(r) + "\n" for r in rows))
    base, agent = out["baseline"], out[best["version"]]
    headline = {
        "version": best["version"],
        "baseline_median_s": round(base["median_s"], 1),
        "agent_median_s": round(agent["median_s"], 1),
        "baseline_slow_median_s": round(base["slow_median_s"], 1),
        "agent_slow_median_s": round(agent["slow_median_s"], 1),
        "slow_n": agent["slow_n"],
        "actual_median_s": base["actual_median_s"],
        "calibration_error_pct": round(100 * (base["median_s"] - base["actual_median_s"]) / base["actual_median_s"], 1),
        "vs_baseline": agent["vs_baseline"],
        "n": agent["n"],
        "forced": force,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    write_atomic(HEADLINE, json.dumps(headline, indent=1))
    print(json.dumps(headline, indent=1))
    return headline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=1)
    ap.add_argument("--backend", choices=["api", "cli", "stub"], default="api" if os.environ.get("ANTHROPIC_API_KEY") else "cli")
    ap.add_argument("--final", action="store_true", help="evaluate the best version on the frozen week once")
    ap.add_argument("--force", action="store_true", help="with --final, evaluate the frozen week again")
    ap.add_argument("--eval", metavar="POLICY", help=argparse.SUPPRESS)
    ap.add_argument("--splits", default="train,holdout", help=argparse.SUPPRESS)
    ap.add_argument("--diagnostics", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--out", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.eval:
        splits = a.splits.split(",")
        if "frozen" in splits:
            raise SystemExit("the frozen week is only evaluated by --final")
        result = evaluate(a.eval, splits, a.diagnostics)
        Path(a.out).write_text(json.dumps(result, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
        return
    if a.final:
        final(a.force)
        return
    start = len(load_versions())
    for i in range(a.iters):
        iterate(a.backend, start + i)


if __name__ == "__main__":
    main()
