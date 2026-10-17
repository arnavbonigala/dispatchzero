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
.venv/bin/uvicorn --app-dir backend api:app --port 8000
.venv/bin/python backend/agent.py --iters 4
```

`fetch.py` downloads the last 5 full weeks of SF ambulance calls (weeks 1 to 3 train, week 4 holdout, week 5 frozen), ambulance posts, unit home posts, SF Caltrans cameras, and the OpenStreetMap road graph into `data/` (gitignored). Pass `--skip-graph` to skip the road graph.

## Layout

| Path | Owner | What |
|---|---|---|
| `backend/contracts.py` | All | Frozen shapes: `Call`, `IdleUnit`, `CallResult`, `Summary`, `Version`, `CameraReading`, policy signatures |
| `backend/fetch.py` | A | Data download |
| `backend/sim.py` | A | `replay(calls, policy, slowdowns, version, split) -> (results, summary)`. Replays real calls over the OSM road graph with unit shifts, background load, queueing and relocation time. CLI: `--policy NAME_OR_PATH --split train\|holdout\|fit\|frozen [--compare baseline] [--out FILE]`, `--calibrate`, `--fit-speeds`, `--worst N`, `--jam-test CAMERA_ID [--slowdown F]`, `--offload-cap MINUTES`, `--dispatch-at-entry`, `--self-check`, `--fixtures` |
| `backend/policies/baseline.py` | A | Reference policy: each unit at its home post, closest idle unit sent |
| `backend/agent.py` | C | Improvement loop: `--iters N [--model sonnet]`, `--headline` |
| `backend/api.py` | C | FastAPI: `uvicorn --app-dir backend api:app --port 8000` |
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
- Source is checked before loading: imports limited to `math`, `statistics`, `collections`, `itertools`, `functools`, `heapq`, `bisect`, `random`; no file access, `eval`/`exec`, `getattr`, or underscore attributes. Violations raise `PolicyRejected`.
- `choose_unit` must return an idle unit's id. Anything else, or an exception, falls back to the closest idle unit and is counted in `stats`.
- Moving a unit to a new post takes its drive time between posts, starting at the hour boundary. It stays available while moving and is dispatched from its old post until it arrives.
- After each call the unit becomes free where it actually finished: at the nearest emergency hospital (`data/hospitals.json`, from OpenStreetMap) if the call record shows a transport, otherwise at the scene. It is available right away and is dispatched from the post nearest that spot until it has driven back to its own post.

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

## Hospital handoff what-if

A transported patient's ambulance spends a median 43 minutes between arriving at the hospital and being available again (`hospital_s` in `calls.parquet`; 97% of transports take over 20 minutes). `sim.py --offload-cap MINUTES --split holdout [--policy NAME]` replays the same calls with that time capped and compares:

| Cap | Everyone (median) | Average | Slow neighborhoods | 90th percentile |
|---|---|---|---|---|
| 30 min | −10 s (CI −16 to −5) | −16 s | −31 s | −25 s |
| 20 min | −20 s (CI −28 to −15) | −26 s | −34 s | −43 s |

Caveat: `hospital_s` covers the whole hospital stay (offload, paperwork, cleanup), while SF's 20-minute standard (EMSA Policy 4000.1) covers patient offload alone at the 90th percentile. Per-hospital offload data: DataSF `6x9q-izga` (monthly 90th-percentile offload by hospital) and `t4sf-777q` (diversions).

## Dispatch-at-entry what-if

The 148 s median from call received to dispatch splits into received → entry (call-taking, median 98 s) and entry → dispatch (median 37 s). `sim.py --dispatch-at-entry --split holdout` replays the calls with each unit dispatched at entry instead, for example by an AI pre-alert as soon as the call is entered:

| Scenario | Everyone (median) | Average | Slow neighborhoods |
|---|---|---|---|
| Dispatch at entry | −62 s (CI −69 to −55) | −90 s | −52 s |
| Dispatch at entry + 20-min hospital cap | −80 s (CI −89 to −73) | −116 s | −78 s |

Caveat: some entry → dispatch time is real triage or waiting for a free unit, so this is an upper bound on what faster dispatch could save.

## Paramedic engines

SF sends a fire engine to only 43% of ambulance calls, and 34 of its engines carry a paramedic. When one goes, it beats the ambulance about 75% of the time, by a median of about 2.7 minutes. `sim.py --engines` adds those engines: each waits at its own station, stays busy with its real fire and other calls (`data/engine_dispatches.parquet`), and the score becomes the time to the first paramedic on scene, by engine or by ambulance. It is calibrated against history without replay tuning: engine offsets come from fit weeks only. The simulated first-paramedic median is 550 s vs a real 548 s on fit and 557 s vs 560 s on frozen.

A policy can add:

```python
def send_engine(call: Call, engines: list[IdleUnit], eta, ambulance_s: float) -> str | None
```

`engines` are idle paramedic engines (`kind: "engine"`, so `eta` uses engine timing), `ambulance_s` is the chosen ambulance's time from dispatch to scene, and `ENGINES` (engine to station post) is injected. Without `send_engine`, the simulator sends the nearest idle engine exactly when history did. `stats.engine_conflicts` counts real engine calls that start while the simulator has that engine on a medical call, which is the price of sending more.

Frozen week, first paramedic on scene, paired against the baseline:

| Policy | Everyone (median) | Slow neighborhoods | Under 8 min | Engine conflicts / week |
|---|---|---|---|---|
| Baseline (history's engine use) | 557 s | | 37% | 24 |
| `reference_engine_3min`: send if 3+ min ahead of the ambulance | −75 s (CI −90 to −63) | −123 s (CI −147 to −88) | 49% | 54 |
| `reference_engine`: send if ahead of the ambulance | −127 s (CI −140 to −116) | −148 s (CI −169 to −115) | 66% | 90 |

## Agent context

`sim.py --policy NAME --split train --worst 50 --out FILE` returns the 50 calls with the most controllable time (queue wait + drive), each with the closest post, its drive time, which units were based there and whether they were idle, busy or off duty, plus `avoidable_s`, and aggregates: worst calls by neighborhood and bucket, and the top (neighborhood, bucket) cells by total avoidable seconds.

Limits to state honestly: SF publishes no unit locations, so the simulator assumes idle units wait at posts. Per-call simulated drive time correlates only weakly with real drive time (Spearman 0.19, median absolute error about 245s), and the factors also absorb units not being at their post. Present gains as simulator-relative estimates calibrated to SF's real medians, not as predicted real-world seconds.

## Agent loop

`agent.py --iters N` runs N rounds. Each round:

1. Takes the newest accepted version in `runs/versions.jsonl` as the current best (`baseline` on the first run).
2. Asks Claude through the `claude` CLI (`--model`, default `sonnet`, no tools). The prompt includes the policy contract from this README, the current best's code, its training summary, the `--worst 50` context, the last 5 attempts, and the closest rejected candidate's code. Holdout numbers never go into the prompt; a holdout failure shows up only as "failed the holdout gate".
3. Writes the reply to `backend/policies/vNNN.py` and scores it with `sim.py` in a subprocess (120 s timeout). A crash, timeout, or `PolicyRejected` rejects the candidate.
4. Gate 1, train: the slow-neighborhood median must be below the current best's and citywide at most 5 s slower than baseline. Gate 2, holdout: `--compare baseline` must return `passes`.
5. Appends `{version, parent, diagnosis, train_median, frozen_median, accepted, reason, ...}` to `runs/versions.jsonl`. `frozen_median` is filled only for accepted versions and is never shown to the agent.

`agent.py --headline` scores the newest accepted version once on the frozen week against baseline and writes `runs/headline.json`.

## API

| Route | Returns |
|---|---|
| `GET /versions` | `runs/versions.jsonl` as a list, or the fixture |
| `GET /headline` | `runs/headline.json` |
| `GET /summary/{version}?split=frozen` | Summary for `baseline` or `vNNN` on `train`, `holdout` or `frozen` |
| `GET /results/{version}?split=frozen` | Per-call results for the replay |
| `GET /policy/{version}` | Policy source |
| `GET /cameras` | `data/camera_readings.json`, or the fixture |
| `GET /posts`, `/slow`, `/speeds` | `data/` files |

Results are cached in `runs/results/{version}_{split}.json`. Delete that folder after any change to default scoring. The Vite dev server proxies `/api` to port 8000.
