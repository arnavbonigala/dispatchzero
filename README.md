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

`eta(unit, call)` returns drive seconds from the unit's post to the call. The module also gets two injected globals: `HOME` (unit to home post) and `POSTS` (list of `{post, station_area, lat, lon}`).

Rules the simulator enforces:
- Policies only see dispatcher-visible call fields: an opaque per-run `call_id`, `t`, `lat`, `lon`, `hour`, `nbhd`, `station_area`, `priority`.
- Source is checked before loading: imports limited to `math`, `statistics`, `collections`, `itertools`, `functools`, `heapq`, `bisect`, `random`; no file access, `eval`/`exec`, `getattr`, or underscore attributes. Violations raise `PolicyRejected`.
- `choose_unit` must return an idle unit's id. Anything else, or an exception, falls back to the closest idle unit and is counted in `stats`.
- Moving a unit to a new post in `post_plan` costs its drive time between posts, starting at the hour boundary.
- After each call the unit drives back to its post before it is available again.

## Splits and reporting

- `train` (weeks 1 to 3): the agent diagnoses from these.
- `holdout` (week 4): the agent's promotion gate.
- `frozen` (week 5): touched only for the final headline, reported with `--compare baseline` (paired bootstrap 95% CI). The frozen baseline median has about ±28s of sampling noise, so gains smaller than that are not real.

## Calibration

OSM speed-limit travel times are scaled by one factor per time-of-day bucket so the baseline policy's simulated median drive time matches SF's actual median on the training weeks. Fit on weeks 1 to 4 with learned road speeds: baseline sim median 679s vs actual 663s (+2.4%); frozen week 679s vs 658s (+3.2%). Bucket factors are about 1.7 to 2.1.

## Learned road speeds

`--fit-speeds` learns one multiplier per (destination neighborhood × time bucket), about 200 cells, from each training call's real drive time against the route time from the dispatched unit's home post. Cells with few calls are shrunk toward 1 (weight 30 calls). The multipliers are adopted only if they lower the median drive-time error on the holdout week, then calibration is re-run. Current result: holdout error 287s → 244s (adopted). Fastest: Sunset/Parkside (~0.67×); slowest: Tenderloin and South of Market (2.5 to 3.7×, which also reflects units rarely being at their post downtown).

`data/speeds.json` holds `adopted`, the error `report`, `rel` (`"Neighborhood|bucket": multiplier`), and `nbhd_centers` (`"Neighborhood": [lat, lon]`) for the UI heatmap.

## Agent context

`sim.py --policy NAME --split train --worst 50 --out FILE` returns the 50 calls with the most controllable time (queue wait + drive), each with the closest post, its drive time, which units were based there and whether they were idle, busy or off duty, plus `avoidable_s`, and aggregates: worst calls by neighborhood and bucket, and the top (neighborhood, bucket) cells by total avoidable seconds.

Limits to state honestly: SF publishes no unit locations, so the simulator assumes idle units wait at posts. Per-call simulated drive time correlates only weakly with real drive time (Spearman 0.19, median absolute error about 245s), and the factors also absorb units not being at their post. Present gains as simulator-relative estimates calibrated to SF's real medians, not as predicted real-world seconds.

## Agent loop

```bash
cd backend
../.venv/bin/python agent.py --iters 8      # Claude rewrites the policy 8 times
../.venv/bin/python agent.py --final        # once, at the end: frozen week vs baseline
```

Each iteration evaluates the current best on the training weeks, then sends Claude (`claude-sonnet-5-5`) the policy code, the training summary, the worst 50 calls from `sim.worst_calls`, per-hour and per-area tables, per-area demand, units on shift by hour, `POSTS`, `HOME` and the last 5 diagnoses. Claude replies with a `<diagnosis>` and a full policy file. The candidate is source-checked, saved as `backend/policies/vNNN.py` and run in a subprocess with a 10 minute timeout. It is promoted only if it beats the current best's median on the training weeks (gate 1) and then on the holdout week (gate 2), and it is rejected if more than 2% of its calls hit a policy error. The agent sees holdout results only as pass or fail and never sees the frozen week.

`--backend api` (the default when `ANTHROPIC_API_KEY` is set) uses the Anthropic SDK. `--backend cli` uses a logged-in `claude` CLI in print mode with no tools. `--backend stub` returns canned policies, for testing the loop without spending tokens. Prompts and replies are saved under `backend/runs/llm/`.

`--final` runs the best accepted version and the baseline on the frozen week (`sim.py --compare baseline`), fills `frozen_median` for those two rows of `versions.jsonl` and writes `backend/runs/headline.json`.

## API

| Route | Returns |
|---|---|
| `GET /versions` | `{source, versions}` from `runs/versions.jsonl`, else `fixtures/versions.jsonl`. Rows add `holdout_median`, `reason`, `stats` and `ts` to the `Version` contract |
| `GET /headline` | Frozen-week result from `runs/headline.json`: baseline and agent medians, paired CI, calibration error |
| `GET /runs` | Frozen-week summaries written by `--final` |
| `GET /replay/{version}?split=frozen&day=YYYY-MM-DD` | `CallResult` rows sorted by time, plus the list of `days`. Computed with the simulator and cached in `runs/`; falls back to `fixtures/results.json` for the baseline |
| `GET /posts` | Ambulance posts |
| `GET /cameras` | `CameraReading` list from `runs/cameras.json` (where `cameras.py` should write), else fixtures |
| `WS /live` | Pushes `{type: "versions" \| "cameras", ...}` whenever either file changes |

Every response carries `source: "live" | "fixtures"`. The UI reads `VITE_API_URL` (default `http://localhost:8000`).
