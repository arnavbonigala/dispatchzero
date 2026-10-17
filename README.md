# Golden Minute

A self-improving ambulance dispatcher, tested on San Francisco's real 911 history and fed by live traffic cameras. See `OVERVIEW.md` for the idea and `GOLDEN_MINUTE.md` for the build plan.

## Setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python backend/fetch.py
.venv/bin/python backend/sim.py --calibrate
.venv/bin/python backend/sim.py --fit-speeds
.venv/bin/python backend/sim.py --self-check
cd frontend && npm install && npm run dev
```

`fetch.py` downloads the last 5 full weeks of SF ambulance calls (weeks 1 to 3 train, week 4 holdout, week 5 frozen), ambulance posts, unit home posts, SF Caltrans cameras, and the OpenStreetMap road graph into `data/` (gitignored). Pass `--skip-graph` to skip the road graph.

## Layout

| Path | Owner | What |
|---|---|---|
| `backend/contracts.py` | All | Frozen shapes: `Call`, `IdleUnit`, `CallResult`, `Summary`, `Version`, `CameraReading`, policy signatures |
| `backend/fetch.py` | A | Data download |
| `backend/sim.py` | A | `replay(calls, policy, slowdowns, version, split) -> (results, summary)`. Replays real calls over the OSM road graph with unit shifts, background load, queueing and relocation time. CLI: `--policy NAME_OR_PATH --split train\|holdout\|fit\|frozen [--compare baseline] [--out FILE]`, `--calibrate`, `--fit-speeds`, `--worst N`, `--jam-test CAMERA_ID [--slowdown F]`, `--self-check`, `--fixtures` |
| `backend/policies/baseline.py` | A | Reference policy: each unit at its home post, closest idle unit sent |
| `backend/agent.py` | C | Improvement loop: `--iters N [--backend api\|cli\|stub]`, `--final`. Writes `backend/policies/vNNN.py` and `backend/runs/versions.jsonl` |
| `backend/api.py` | C | FastAPI for the UI (`uvicorn api:app --port 8000` from `backend/`) |
| `backend/cameras.py` | B | Caltrans → YOLO → `CameraReading` |
| `fixtures/` | B, C | Sample `results.json`, `summary.json`, `versions.jsonl`, `cameras.json`, `posts.json` in contract shape. Regenerate with `python backend/sim.py --fixtures` |
| `frontend/` | B | Vite + React + Leaflet |

## Policy contract

A policy is a Python file in `backend/policies/` with two functions:

```python
def post_plan(hour: int, units: list[str]) -> dict[str, int]
def choose_unit(call: Call, idle: list[IdleUnit], eta) -> str
```

`post_plan` is called at every hour boundary with the units on shift at that moment; units it leaves out keep their current post. `eta(unit, call)` returns drive seconds from the unit's current position to the call.

Injected globals:
- `HOME`: unit to home post (the baseline layout, see below)
- `POSTS`: list of `{post, station_area, lat, lon}`
- `DEMAND`: `{bucket: {post: calls}}` from the training weeks, each call counted at its nearest post
- `POST_DRIVE`: road drive seconds between posts, `POST_DRIVE[from][to]`
- `BUCKET_OF`: hour to time bucket (`night`, `morning_rush`, `midday`, `evening_rush`, `evening`)

Rules the simulator enforces:
- Policies only see dispatcher-visible call fields: an opaque per-run `call_id`, `t`, `lat`, `lon`, `hour`, `nbhd`, `station_area`, `priority`.
- Source is checked before loading: imports limited to `math`, `statistics`, `collections`, `itertools`, `functools`, `heapq`, `bisect`, `random`, and a module may only be used as `module.function` (no submodules such as `statistics.sys`, no passing modules around); no file access, `eval`/`exec`, `getattr`, underscore attributes, or frame attributes like `gi_frame`. Violations raise `PolicyRejected`.
- `choose_unit` must return an idle unit's id. Anything else, or an exception, falls back to the closest idle unit and is counted in `stats`.
- Moving a unit to a new post takes its drive time between posts, starting at the hour boundary. It stays available while moving and is dispatched from its old post until it arrives.
- After each call the unit drives back to its post before it is available again.

## Baseline

`units.json` spreads the 57 units across posts in proportion to training-week call volume by station area (largest remainder). The baseline policy keeps every unit at that home post and sends the closest idle unit. `unit_areas.json` keeps each unit's most frequent station area, used only to fit road speeds.

## The goal

The agent's job is to make SF's 10 slowest neighborhoods faster without slowing everyone else. `sim.py --calibrate` picks them from the baseline on the training weeks and saves `data/slow_nbhds.json`. Every summary includes `slow_median_s` and `slow_n`. `--compare baseline` returns:

- `slow_nbhds`: paired median difference and 95% CI for calls in those neighborhoods
- `everyone`: the same for all calls
- `passes`: true when the slow-neighborhood CI is entirely below zero and the citywide median is at most 5 s slower (`OVERALL_TOLERANCE_S`)

## Splits and reporting

- `train` (weeks 1 to 3): the agent diagnoses from these.
- `holdout` (week 4): the agent's promotion gate.
- `frozen` (week 5): touched only for the final headline, reported with `--compare baseline` (paired bootstrap 95% CI). The frozen baseline median has about ±28s of sampling noise, so gains smaller than that are not real.

## Calibration

Simulated drive = OSM road time × learned neighborhood multiplier + one offset per time bucket. The offsets (about 290 to 340s) are fit so the baseline's median simulated drive matches SF's median real drive on weeks 1 to 4. They stand for everything the road graph misses, mostly units not sitting exactly at their post. Because the offset is additive, seconds saved by better positioning count 1:1 and are not amplified.

Current fit: baseline sim median 657s vs actual 663s on weeks 1 to 4 (−1.0%); frozen week 664s vs 658s (+0.9%).

Limits to state honestly: SF publishes no unit locations, so the simulator assumes idle units wait at posts. Per-call simulated drive time correlates only weakly with real drive time (Spearman 0.14, median absolute error about 205s). Medians match; individual calls don't. Present gains as simulator-relative estimates calibrated to SF's real medians.

## Learned road speeds

`--fit-speeds` learns one multiplier per (destination neighborhood × time bucket), about 200 cells, in the additive model: `real drive ≈ route time from the unit's usual post × multiplier + bucket offset`, fit on weeks 1 to 3. Cells with few calls shrink toward 1 (weight 30 calls), and multipliers are capped to 0.5 to 2×. They are adopted only if they lower median drive-time error on the holdout week, then calibration is re-run. Current result: holdout error 206s → 196s (adopted). Fastest: Outer Richmond, Bernal Heights, Sunset (about 0.72×); slowest: Tenderloin (capped at 2×).

`data/speeds.json` holds `adopted`, the error `report`, `rel` (`"Neighborhood|bucket": multiplier`), and `nbhd_centers` (`"Neighborhood": [lat, lon]`) for the UI heatmap.

## Cameras in the simulator

- `edge_slowdowns(readings, roads)` turns `CameraReading`s into `{edge_index: factor}`: road edges within 600 m of the camera whose OSM `ref` matches the camera's route (`US-101` → `US 101`, `SR-1` → `CA 1`) are slowed by the camera's `slowdown`. Readings with `slowdown <= 1` change nothing.
- `World.load(slowdowns=...)` rebuilds travel times with those slowdowns.
- `Roads.path(src_node, dst_node, slowdowns)` returns `(seconds, [[lat, lon], ...])` for drawing a route on the map.
- `--jam-test CAMERA_ID` measures the direct effect of a jam on the holdout week: calls whose best route crosses the jam, calls that should be sent from a different post, and seconds saved by switching versus dispatching blind.

Finding: in-city ambulance routes rarely use the freeways Caltrans covers. At 4× slowdown, most SF cameras affect 0 to 14 calls a week; SR-1 near the Presidio Tunnel is the clearest case (2 calls switched, about 128 s saved each). Camera value is mostly a live-demo story, not a measurable weekly gain.

## Agent context

`sim.py --policy NAME --split train --worst 50 --out FILE` returns the 50 calls with the most controllable time (queue wait + drive), each with the closest post, its drive time, which units were based there and whether they were idle, busy or off duty, plus `avoidable_s`, and aggregates: worst calls by neighborhood and bucket, and the top (neighborhood, bucket) cells by total avoidable seconds.

Limits to state honestly: SF publishes no unit locations, so the simulator assumes idle units wait at posts. Per-call simulated drive time correlates only weakly with real drive time (Spearman 0.19, median absolute error about 245s), and the factors also absorb units not being at their post. Present gains as simulator-relative estimates calibrated to SF's real medians, not as predicted real-world seconds.

## Agent loop

```bash
cd backend
../.venv/bin/python agent.py --iters 8      # Claude rewrites the policy 8 times
../.venv/bin/python agent.py --final        # once, at the end: frozen week vs baseline
```

Each iteration re-runs the current best and the baseline on the training and holdout weeks in a subprocess, so gates always compare like with like even after recalibration. It sends Claude (`claude-sonnet-5-5`) the goal (the slow neighborhoods from `slow_nbhds.json`), the policy code, the training summary next to the baseline, the worst 50 calls from `sim.worst_calls`, per-neighborhood, per-hour and per-area tables, per-area demand, units on shift by hour, `POSTS`, `HOME` and the last 5 diagnoses. Claude replies with a `<diagnosis>` and a full policy file. The candidate is source-checked, saved as `backend/policies/vNNN.py` and run in a subprocess with a 10 minute timeout, no credentials in its environment, and `random` seeded so runs are repeatable.

Promotion:
- Rejected if `post_plan` raises for any hour or `choose_unit` fails on more than 2% of calls.
- Gate 1 (training weeks): slow-neighborhood median at least 2 s below the current best's, and citywide median at most 5 s above the baseline's.
- Gate 2 (holdout week): slow-neighborhood median below the current best's, and `sim.goal` vs baseline `passes` (slow-neighborhood 95% CI below zero, citywide at most 5 s slower).

The agent sees holdout results only as pass or fail and never sees the frozen week.

`--backend api` (the default when `ANTHROPIC_API_KEY` is set) uses the Anthropic SDK. `--backend cli` uses a logged-in `claude` CLI in print mode with no tools. `--backend stub` returns canned policies, for testing the loop without spending tokens. Prompts and replies are saved under `backend/runs/llm/`.

`--final` runs the best accepted version and the baseline on the frozen week (`sim.py --compare baseline`), fills `frozen_median` and `frozen_slow_median` for those two rows of `versions.jsonl` and writes `backend/runs/headline.json`. It refuses to run again once `headline.json` exists unless given `--force`, and records `forced` in the headline.

## API

| Route | Returns |
|---|---|
| `GET /versions` | `{source, versions}` from `runs/versions.jsonl`, else `fixtures/versions.jsonl`. Rows add `holdout_median`, `train_slow_median`, `holdout_slow_median`, `frozen_slow_median`, `holdout_passes`, `reason`, `stats` and `ts` to the `Version` contract |
| `GET /headline` | Frozen-week result from `runs/headline.json`: baseline and agent medians, paired CI, calibration error |
| `GET /runs` | Frozen-week summaries written by `--final` |
| `GET /replay/{version}?split=frozen&day=YYYY-MM-DD` | `CallResult` rows sorted by time, plus the list of `days`, for the baseline and accepted versions only. The frozen week comes only from `--final` runs (fixtures for the baseline before that); train and holdout replays run `sim.py` in a subprocess with a timeout and are cached in `runs/` |
| `GET /posts` | Ambulance posts |
| `GET /cameras` | `CameraReading` list from `runs/cameras.json` (where `cameras.py` should write), else fixtures |
| `WS /live` | Pushes `{type: "versions" \| "cameras", ...}` whenever either file changes |

Every response carries `source: "live" | "fixtures"`. The UI reads `VITE_API_URL` (default `http://localhost:8000`).
