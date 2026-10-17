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
| `backend/sim.py` | A | `replay(calls, policy, slowdowns, version, split) -> (results, summary)`. Replays real calls over the OSM road graph with unit shifts, background load, queueing and relocation time. CLI: `--policy NAME_OR_PATH --split train\|holdout\|fit\|frozen [--compare baseline] [--out FILE]`, `--calibrate`, `--fit-speeds`, `--worst N`, `--self-check`, `--fixtures` |
| `backend/policies/baseline.py` | A | Reference policy: each unit at its home post, closest idle unit sent |
| `backend/agent.py` | C | Improvement loop |
| `backend/api.py` | C | FastAPI |
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
- After each call the unit drives back to its post before it is available again.

## Baseline

`units.json` spreads the 57 units across posts in proportion to training-week call volume by station area (largest remainder). The baseline policy keeps every unit at that home post and sends the closest idle unit. `unit_areas.json` keeps each unit's most frequent station area, used only to fit road speeds.

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

## Agent context

`sim.py --policy NAME --split train --worst 50 --out FILE` returns the 50 calls with the most controllable time (queue wait + drive), each with the closest post, its drive time, which units were based there and whether they were idle, busy or off duty, plus `avoidable_s`, and aggregates: worst calls by neighborhood and bucket, and the top (neighborhood, bucket) cells by total avoidable seconds.

Limits to state honestly: SF publishes no unit locations, so the simulator assumes idle units wait at posts. Per-call simulated drive time correlates only weakly with real drive time (Spearman 0.19, median absolute error about 245s), and the factors also absorb units not being at their post. Present gains as simulator-relative estimates calibrated to SF's real medians, not as predicted real-world seconds.
