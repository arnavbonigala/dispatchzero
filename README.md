# Golden Minute

A self-improving ambulance dispatcher, tested on San Francisco's real 911 history and fed by live traffic cameras. See `OVERVIEW.md` for the idea and `GOLDEN_MINUTE.md` for the build plan.

## Setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python backend/fetch.py
.venv/bin/python backend/sim.py --calibrate
.venv/bin/python backend/sim.py --self-check
cd frontend && npm install && npm run dev
```

`fetch.py` downloads the last 5 full weeks of SF ambulance calls (weeks 1 to 4 train, week 5 frozen), ambulance posts, unit home posts, SF Caltrans cameras, and the OpenStreetMap road graph into `data/` (gitignored). Pass `--skip-graph` to skip the road graph.

## Layout

| Path | Owner | What |
|---|---|---|
| `backend/contracts.py` | All | Frozen shapes: `Call`, `IdleUnit`, `CallResult`, `Summary`, `Version`, `CameraReading`, policy signatures |
| `backend/fetch.py` | A | Data download |
| `backend/sim.py` | A | `replay(calls, policy, slowdowns, version, split) -> (results, summary)`. Replays real calls over the OSM road graph with unit shifts, background load, queueing and relocation time. CLI: `--policy NAME --split train\|frozen --out FILE`, `--calibrate`, `--self-check`, `--fixtures` |
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

`eta(unit, call)` returns drive seconds from the unit's post to the call. Policies only see dispatcher-visible call fields: `call_id`, `t`, `lat`, `lon`, `hour`, `nbhd`, `station_area`, `priority`. Moving a unit to a new post in `post_plan` costs its drive time between posts, during which it is unavailable.

## Calibration

OSM speed-limit travel times are scaled by one factor per time-of-day bucket so the baseline policy's simulated median drive time matches SF's actual median on the training weeks. Current fit: baseline sim median 680s vs actual 663s on train (+2.6%), 697s vs 658s on the frozen week (+6.0%). Factors are about 1.9 to 2.3, so they also absorb units not being at their post when dispatched.
