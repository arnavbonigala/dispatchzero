"""Improvement loop: Claude rewrites the dispatch policy, and a candidate is promoted only if it
beats the current best on the training weeks (gate 1) and on the holdout week (gate 2).
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
MAX_POLICY_ERROR_RATE = 0.02
WORST_N = 50
HISTORY_N = 5

SYSTEM = """You are the policy engineer for Golden Minute, an ambulance dispatcher for San Francisco.
You write a Python dispatch policy that a simulator replays against real 911 medical calls. Your goal is a lower
median response time (call received to ambulance on scene). You control only two things: where each idle unit
waits at each hour of the day, and which idle unit is sent to each call. Processing and turnout delays are fixed.

The policy file must define exactly these two functions:

    def post_plan(hour: int, units: list[str]) -> dict[str, int]   # unit_id -> post index
    def choose_unit(call: dict, idle: list[dict], eta) -> str        # returns one idle unit_id

Facts about the simulator:
- `post_plan(hour, units)` is called once per hour of day (0-23) and the answer is cached and reused for every day.
  Units missing from the returned dict stay where they are. Moving a unit to a new post costs its drive time between
  posts, starting at the hour boundary (a busy unit moves after it finishes). Every relocation makes that unit
  unavailable while it drives, so moving many units every hour has a real cost.
- After each call the unit drives back to its current post before it is available again.
- Only units on shift are offered in `idle`; shift patterns come from the real data.
- `call` has only: call_id (opaque), t (ISO local time string like "2026-10-06T17:04:17"), lat, lon, hour, nbhd,
  station_area, priority ("1", "2", "3" or "E"; lower number is more urgent).
- `idle` is a list of {"unit_id": str, "post": int}. `eta(unit, call)` returns drive seconds from that unit's post.
- If no unit is idle the call queues for the first unit to free up, regardless of the policy.
- Two globals are injected before your module runs: HOME (dict unit_id -> home post index) and POSTS (list of
  {"post", "station_area", "lat", "lon"}). Post index i is POSTS[i].
- Allowed imports: math, statistics, collections, itertools, functools, heapq, bisect, random. No file access, no
  eval/exec, no getattr, no names or attributes starting with an underscore. Violations reject the policy.
- If choose_unit raises or returns something that is not an idle unit_id, the simulator sends the closest idle unit
  and counts an error. Keep the code fast: it runs for every call.
- The policy cannot read data at runtime, so any per-area or per-hour knowledge must be written into the file as
  constants.

Every change is tested on calls you have not seen. Prefer changes that generalize (coverage of under-served areas,
keeping reserve units where demand is high) over fitting individual calls.

Reply in exactly this format:
<diagnosis>One to three plain sentences a dispatcher could read aloud: what was slow, where and when, and what the
new policy changes. Name neighborhoods, hours and units.</diagnosis>
```python
# the complete new policy file
```"""


def load_versions():
    if not VERSIONS.exists():
        return []
    return [json.loads(line) for line in VERSIONS.read_text().splitlines() if line.strip()]


def append_version(row):
    RUNS.mkdir(exist_ok=True)
    with VERSIONS.open("a") as f:
        f.write(json.dumps(row) + "\n")


def rewrite_versions(rows):
    RUNS.mkdir(exist_ok=True)
    VERSIONS.write_text("".join(json.dumps(r) + "\n" for r in rows))


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


class Recorder:
    """Wraps a loaded policy and remembers who was idle and the closest idle unit for each call."""

    def __init__(self, policy):
        self.policy = policy
        self.seen = {}

    def post_plan(self, hour, units):
        return self.policy.post_plan(hour, units)

    def choose_unit(self, call, idle, eta):
        try:
            etas = [(eta(u, call), u["unit_id"], u["post"]) for u in idle]
            best = min(etas)
            self.seen[call["call_id"]] = {"idle": len(idle), "nearest_idle": best[1], "nearest_idle_post": best[2], "nearest_idle_eta_s": round(best[0], 1)}
        except Exception:
            pass
        return self.policy.choose_unit(call, idle, eta)


def evaluate(policy, splits):
    import sim

    world = sim.World.load()
    mod = sim.load_policy(policy, world)
    posts = world.posts
    out = {}
    for split in splits:
        calls = sim.load_calls(split)
        rec = Recorder(mod)
        results, stats = sim.simulate(world, calls, rec)
        summary = sim.summarize(results, Path(str(policy)).stem, split)
        entry = {"summary": summary, "stats": stats}
        if split == "train":
            entry["diagnostics"] = diagnostics(world, calls, results, rec.seen, posts)
        out[split] = entry
    return out


def diagnostics(world, calls, results, seen, posts):
    import pandas as pd

    by_id = {c["call_id"]: c for c in calls}
    rows = []
    for i, r in enumerate(results):
        c = by_id[r["call_id"]]
        rows.append({**r, "station_area": c["station_area"], "priority": c["priority"], **seen.get(f"call{i}", {"idle": 0})})
    n_posts = len(world.post_nodes)

    worst = []
    for r in sorted(rows, key=lambda x: -x["response_s"])[:WORST_N]:
        node = world.call_nodes[r["call_id"]]
        closest = min(range(n_posts), key=lambda p: world.drive(p, node, r["hour"]))
        ts = pd.Timestamp(r["t"])
        worst.append({
            "when": ts.strftime("%a %H:%M"),
            "nbhd": r["nbhd"],
            "area": r["station_area"],
            "pri": r["priority"],
            "unit": r["unit"],
            "from_post": r["from_post"],
            "from_area": posts[r["from_post"]]["station_area"],
            "eta_s": round(r["eta_s"]),
            "wait_s": round(r["wait_s"]),
            "response_s": round(r["response_s"]),
            "idle_units": r["idle"],
            "nearest_idle": r.get("nearest_idle"),
            "nearest_idle_eta_s": round(r["nearest_idle_eta_s"]) if "nearest_idle_eta_s" in r else None,
            "closest_post": closest,
            "closest_post_eta_s": round(world.drive(closest, node, r["hour"])),
        })

    def table(key):
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

    bucket_of = {h: b for b, hs in [("night", range(0, 7)), ("morning_rush", range(7, 10)), ("midday", range(10, 16)), ("evening_rush", range(16, 19)), ("evening", range(19, 24))] for h in hs}
    demand = defaultdict(lambda: defaultdict(int))
    for c in calls:
        demand[c["station_area"]][bucket_of[c["hour"]]] += 1
    days = len({c["t"][:10] for c in calls}) or 1

    return {
        "worst": worst,
        "by_hour": table(lambda r: r["hour"]),
        "by_area": table(lambda r: r["station_area"]),
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


def run_eval(policy, splits):
    """Run the candidate in a subprocess with a timeout so a hung or crashing policy can't take the loop down."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        out = Path(f.name)
    cmd = [sys.executable, str(Path(__file__)), "--eval", str(policy), "--splits", ",".join(splits), "--out", str(out)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=EVAL_TIMEOUT_S, cwd=BACKEND)
    except subprocess.TimeoutExpired:
        return None, f"timed out after {EVAL_TIMEOUT_S}s"
    try:
        if p.returncode != 0:
            return None, (p.stderr.strip().splitlines() or ["crashed"])[-1][:300]
        return json.loads(out.read_text()), None
    finally:
        out.unlink(missing_ok=True)


# ---------------------------------------------------------------- prompting


def build_prompt(code, train, history, world_info):
    s, d = train["summary"], train["diagnostics"]
    lines = [
        "## Current best policy",
        "```python",
        code.strip(),
        "```",
        "",
        "## Its result on the training weeks",
        json.dumps({k: s[k] for k in ("median_s", "p90_s", "pct_under_8min", "n")} | {"stats": train["stats"]}),
        "",
        "## Recent attempts (newest last; holdout results are pass/fail only)",
    ]
    for h in history:
        verdict = "accepted" if h["accepted"] else f"rejected ({h.get('reason', 'failed a gate')})"
        lines.append(f"- {h['version']} (parent {h['parent'] or '-'}), train median {h.get('train_median')}s, {verdict}: {h['diagnosis']}")
    lines += [
        "",
        f"## The {WORST_N} slowest training calls under the current policy",
        "Each row: when, neighborhood, station area, priority, unit sent, its post and that post's station area, drive",
        "eta, time spent waiting for a free unit, total response, number of idle units at dispatch, the closest idle unit",
        "and its eta, and the closest post of all to the call with its eta (if a unit had been waiting there).",
        *(json.dumps(w) for w in d["worst"]),
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
        "Diagnose the biggest fixable cause of slow responses above, then write the complete improved policy file.",
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
    spread = [2, 4, 8, 16][i % 4]
    return f"""<diagnosis>Stub {i}: most units wait at posts 0 and 2 downtown; spread every unit with index divisible by {spread} to its own post.</diagnosis>
```python
def post_plan(hour, units):
    plan = dict(HOME)
    for i, u in enumerate(sorted(units)):
        if i % {spread} == 0:
            plan[u] = (i * 7) % len(POSTS)
    return {{u: plan[u] for u in units if u in plan}}


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
    tree_names = set(re.findall(r"^def (\w+)\(", code, re.M))
    missing = {"post_plan", "choose_unit"} - tree_names
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
    row = version_row("baseline", "", "Each unit waits at its home post; the closest idle unit is sent.", ev, True, None)
    append_version(row)
    return [row]


def version_row(version, parent, diagnosis, ev, accepted, reason, model=None):
    train = ev["train"] if ev else None
    hold = ev.get("holdout") if ev else None
    return {
        "version": version,
        "parent": parent,
        "diagnosis": diagnosis,
        "train_median": round(train["summary"]["median_s"], 1) if train else None,
        "holdout_median": round(hold["summary"]["median_s"], 1) if hold else None,
        "frozen_median": None,
        "accepted": accepted,
        "reason": reason,
        "train_p90": round(train["summary"]["p90_s"], 1) if train else None,
        "train_pct_under_8min": round(train["summary"]["pct_under_8min"], 4) if train else None,
        "stats": train["stats"] if train else None,
        "model": model,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }


def iterate(backend, i):
    rows = ensure_baseline(load_versions())
    best = best_version(rows)
    code = policy_path(best["version"]).read_text()
    ev_best, err = run_eval(policy_path(best["version"]), ["train"])
    if err:
        raise SystemExit(f"current best {best['version']} failed to evaluate: {err}")
    prompt = build_prompt(code, ev_best["train"], rows[-HISTORY_N:], world_info())
    name = next_name(rows)
    model = "stub" if backend == "stub" else MODEL
    (RUNS / "llm").mkdir(parents=True, exist_ok=True)
    (RUNS / "llm" / f"{name}.prompt.md").write_text(prompt)

    reply, diagnosis, new_code, reason = None, "", None, None
    for attempt in range(2):
        try:
            reply = ask_claude(prompt, backend, i)
            (RUNS / "llm" / f"{name}.reply.md").write_text(reply)
            diagnosis, new_code = parse_reply(reply)
            check_policy(new_code)
            reason = None
            break
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"[:300]
            print(f"{name}: attempt {attempt + 1} unusable ({reason})")
            if reply is None:
                break
            prompt += f"\n\nYour previous reply could not be used: {reason}\nReply again in the required format with a complete, valid policy file."
            reply = None
    if new_code is None or reason:
        row = version_row(name, best["version"], diagnosis or "(no usable reply)", None, False, reason, model)
        append_version(row)
        return row

    path = policy_path(name)
    path.write_text(new_code)
    ev, err = run_eval(path, ["train", "holdout"])
    if err:
        row = version_row(name, best["version"], diagnosis, None, False, f"crashed: {err}", model)
    else:
        stats, n = ev["train"]["stats"], ev["train"]["summary"]["n"]
        bad = (stats["policy_errors"] + stats["invalid_choice"]) / n
        train_m, hold_m = ev["train"]["summary"]["median_s"], ev["holdout"]["summary"]["median_s"]
        if bad > MAX_POLICY_ERROR_RATE:
            reason = f"policy errors on {bad:.1%} of calls"
        elif train_m >= best["train_median"]:
            reason = "failed train gate"
        elif hold_m >= best["holdout_median"]:
            reason = "failed holdout gate"
        row = version_row(name, best["version"], diagnosis, ev, reason is None, reason, model)
    append_version(row)
    verdict = "ACCEPTED" if row["accepted"] else f"rejected: {row['reason']}"
    print(f"{name} (parent {best['version']}): train {row['train_median']} vs {best['train_median']}, {verdict}\n  {diagnosis}")
    return row


def final():
    """Touch the frozen week once: best accepted version vs baseline, with a paired bootstrap CI."""
    rows = load_versions()
    best = best_version(rows)
    if not best:
        raise SystemExit("no versions yet")
    out = {}
    for v in sorted({"baseline", best["version"]}):
        f = RUNS / f"frozen_{v}.json"
        cmd = [sys.executable, str(BACKEND / "sim.py"), "--policy", v, "--split", "frozen", "--out", str(f)]
        if v != "baseline":
            cmd += ["--compare", "baseline"]
        subprocess.run(cmd, check=True, capture_output=True, timeout=EVAL_TIMEOUT_S, cwd=BACKEND)
        out[v] = json.loads(f.read_text())["summary"]
    for r in rows:
        if r["version"] in out:
            r["frozen_median"] = round(out[r["version"]]["median_s"], 1)
    rewrite_versions(rows)
    base, agent = out["baseline"], out[best["version"]]
    calibration = None
    cal_path = BACKEND.parent / "data" / "calibration.json"
    if cal_path.exists():
        calibration = round(100 * (base["median_s"] - base["actual_median_s"]) / base["actual_median_s"], 1)
    headline = {
        "version": best["version"],
        "baseline_median_s": round(base["median_s"], 1),
        "agent_median_s": round(agent["median_s"], 1),
        "actual_median_s": base["actual_median_s"],
        "calibration_error_pct": calibration,
        "baseline_pct_under_8min": round(base["pct_under_8min"], 4),
        "agent_pct_under_8min": round(agent["pct_under_8min"], 4),
        "vs_baseline": agent.get("vs_baseline"),
        "n": agent["n"],
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    HEADLINE.write_text(json.dumps(headline, indent=1))
    print(json.dumps(headline, indent=1))
    return headline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=1)
    ap.add_argument("--backend", choices=["api", "cli", "stub"], default="api" if os.environ.get("ANTHROPIC_API_KEY") else "cli")
    ap.add_argument("--final", action="store_true", help="evaluate the best version on the frozen week once")
    ap.add_argument("--eval", metavar="POLICY", help=argparse.SUPPRESS)
    ap.add_argument("--splits", default="train,holdout", help=argparse.SUPPRESS)
    ap.add_argument("--out", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.eval:
        splits = a.splits.split(",")
        if "frozen" in splits:
            raise SystemExit("the frozen week is only evaluated by --final")
        result = evaluate(a.eval, splits)
        Path(a.out).write_text(json.dumps(result))
        return
    if a.final:
        final()
        return
    for i in range(a.iters):
        iterate(a.backend, len(load_versions()) + i if a.backend == "stub" else i)


if __name__ == "__main__":
    main()
