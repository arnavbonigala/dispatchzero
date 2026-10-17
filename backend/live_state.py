import json
from collections import defaultdict

import numpy as np

import sim

OUT = sim.ROOT / "fixtures" / "engines_state.json"
REACH_S = 480
FIELDS = ["sec", "amb_free", "amb_duty", "eng_free", "reach_pct", "best_drive_s"]


def recorder(world, rows):
    names = list(world.nbhd_nodes)
    nodes = np.array([world.nbhd_nodes[n] for n in names])
    forecast = world.forecast

    def probe(t, call, amb_posts, eng_posts, duty):
        hour = int(call["hour"])
        stamp = str(call["t"])[:13]
        w = np.array([forecast.get(f"{n}|{stamp}", 0.0) for n in names])
        travel = world._mats(hour)[0]
        mult = np.array([world.mult(hour, n) for n in names])
        bucket = sim.BUCKET_OF[hour]
        best = np.full(len(names), np.inf)
        if amb_posts:
            best = np.minimum(best, travel[sorted(set(amb_posts))][:, nodes].min(axis=0) * mult + world.offsets[bucket])
        if eng_posts:
            best = np.minimum(best, travel[sorted(set(eng_posts))][:, nodes].min(axis=0) * mult + world.engine_offsets[bucket])
        total = w.sum() or 1.0
        reach = float((w * (best <= REACH_S)).sum() / total * 100)
        drive = float((w * np.minimum(best, 3600)).sum() / total)
        rows[str(call["t"])[:10]].append([int(t % 86400), len(amb_posts), duty, len(eng_posts), round(reach, 1), round(drive, 1)])

    return probe


def run(world, calls, policy):
    rows = defaultdict(list)
    probe = recorder(world, rows)
    if sim.needs_isolation(policy):
        results, _ = sim.simulate(world, calls, sim.Replay(sim.record_decisions(policy, "frozen", {"engines": True})), engines=True, probe=probe)
    else:
        results, _ = sim.simulate(world, calls, policy, engines=True, probe=probe)
    for day in rows:
        rows[day].sort()
    return dict(rows), results


def hourly(world, calls):
    actual = defaultdict(int)
    for c in calls:
        actual[str(c["t"])[:13]] += 1
    names = list(world.nbhd_nodes)
    out = defaultdict(dict)
    for stamp in sorted({str(c["t"])[:13] for c in calls}):
        expected = sum(world.forecast.get(f"{n}|{stamp}", 0.0) for n in names)
        wx = world.weather.get(stamp, {})
        out[stamp[:10]][int(stamp[11:13])] = [round(expected, 2), actual[stamp], wx.get("rain_mm"), wx.get("temp_c")]
    return out


def main():
    best = json.loads((sim.ROOT / "fixtures" / "engines_headline.json").read_text())["version"]
    world = sim.World.load()
    calls = sim.load_calls("frozen")
    state = {"fields": FIELDS, "reach_s": REACH_S, "hourly": hourly(world, calls), "hourly_fields": ["expected", "actual", "rain_mm", "temp_c"]}
    for name in ("baseline", best):
        rows, results = run(world, calls, name)
        state[name] = rows
        print(name, "mean", round(float(np.mean([r["response_s"] for r in results])), 1),
              "reach", round(float(np.mean([r[4] for d in rows.values() for r in d])), 1))
    state["best"] = best
    OUT.write_text(json.dumps(state, separators=(",", ":")))
    print("wrote", OUT, OUT.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
