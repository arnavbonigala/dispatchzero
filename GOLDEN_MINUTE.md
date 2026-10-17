# Golden Minute: build plan (3 people, 5 hours)

## Context

We're picking a hackathon idea modeled on the winners of the MongoDB SFTW gallery (MongoMation, LabRat, Recursive Harness Scientist, SuperBid). They all shared three traits: grounded in real data or a real runtime, an improvement that held up on data the agent never saw, and a specific domain. Generic "self-improving agent harness" projects (about 40 of 93) didn't place.

**Golden Minute** is a self-improving ambulance dispatcher. Claude writes the dispatch policy as code, replays real San Francisco 911 medical calls through a simulator, diagnoses its slowest responses from the data, and rewrites itself. A new version ships only if it beats the current one on a frozen week it never trained on. Live Caltrans freeway cameras feed congestion into routing, so dispatch reacts to real traffic.

**The one result the demo exists to show:** "On a real week of SF ambulance calls it never saw, the agent's policy reaches patients X seconds faster at the median than the baseline policy, in the same simulator, calibrated to SF's actual response times."

No sponsor tools are required, so storage is local files.

## Focus points (read before writing code)

1. **Measure everything in one simulator.** Never compare agent results to raw historical times directly. Run the baseline policy in our simulator, calibrate it until its median matches SF's actual median, and then compare the agent against that baseline. This is the claim judges will challenge.
2. **The agent controls only two things:** which ambulance to send, and where idle ambulances wait each hour. Call-processing and turnout delays come from the real data for each call, unchanged.
3. **The frozen week is sacred.** The agent never sees its calls, only a single pass/fail number per candidate.
4. **The core loop ships first.** Cameras plug into one function (`edge_slowdowns()`) and can be cut at hour 4 without touching anything else.
5. **Don't build:** accounts, a database server, a road-routing service, multiple cities, fire engines (ambulances only), live dispatch of real calls.

## Data sources (all verified live today)

| Source | Use | Access |
|---|---|---|
| **SF Fire Dept Calls for Service** (`nuek-vuh3`) | Every unit dispatch: `unit_id`, `unit_type`, `received/dispatch/response/on_scene/available_dttm`, `case_location` (lat/lon), `station_area`, `call_type_group`, `priority` | `https://data.sf.gov/resource/nuek-vuh3.json` (SODA API, no key, updated daily). ~300 MEDIC dispatches/day, 56 active medic units, `available_dttm` 99.8% filled |
| **OpenStreetMap SF drive network** | Road graph for travel times | `osmnx.graph_from_place("San Francisco, California", network_type="drive")`, cached to `data/sf.graphml` |
| **Caltrans District 4 CCTV** | 33 in-service SF freeway cameras with lat/lon, live HLS video and 5s JPEG snapshots | `https://cwwp2.dot.ca.gov/data/d4/cctv/cctvStatusD04.json`, filter `county == "San Francisco"` |

Ambulance posts: no station-location dataset is available through the API, so posts are the **median location of calls in each `station_area`** (about 44 points), computed from the data.

## Data slice

- MEDIC rows only. One row per call: the **first MEDIC unit on scene**.
- Response time = `on_scene_dttm − received_dttm`.
- Fixed per-call delays taken from the data: `processing = dispatch − received`, `turnout = response − dispatch`.
- Busy time = `available_dttm − dispatch_dttm`, the historical value for that call.
- Window: the last 5 full weeks. Weeks 1 to 4 train, **week 5 is frozen**. About 2,100 calls per week.

## Architecture

```
data/            calls.parquet, posts.json, sf.graphml, cameras.json, clips/   (gitignored)
backend/
  fetch.py       DataSF → calls.parquet + posts.json; OSM → sf.graphml; Caltrans → cameras.json
  sim.py         replay(calls, policy, slowdowns) → per-call results + summary
  policies/      baseline.py, v001.py, v002.py ... (agent-written)
  agent.py       improvement loop with the frozen-week gate
  cameras.py     poll Caltrans HLS → YOLO → per-camera congestion → edge slowdowns
  api.py         FastAPI: /runs, /versions, /replay/{version}, /cameras, WebSocket /live
  runs/          versions.jsonl, per-run results JSON
frontend/        Vite + React + Leaflet
```

### Contracts (freeze these in the first 20 minutes; both people code against them)

```python
# policy file (what Claude writes)
def post_plan(hour: int, units: list[str]) -> dict[str, int]          # unit_id -> post index
def choose_unit(call: dict, idle: list[dict], eta) -> str              # eta(unit, call) -> seconds
```

```json
// sim result per call (feeds the UI replay and the agent's diagnosis)
{"call_id","t","lat","lon","unit","from_post","eta_s","response_s","historical_s","hour","nbhd"}
// summary
{"version","split","median_s","p90_s","pct_under_8min","n"}
// camera reading
{"camera_id","name","lat","lon","ts","vehicles","moving_frac","slowdown"}
```

## Component details

**Simulator (`sim.py`)**
- Event loop over calls sorted by `received_dttm`.
- Each unit is either idle at its post (from `post_plan` for that hour) or busy until `dispatch + historical busy time`.
- Travel time: Dijkstra over the OSM graph with edge time = length / speed limit (25 mph default) × `city_factor` × camera slowdown.
- Speed-up: precompute single-source travel times from all ~44 posts to every node (44 Dijkstra runs, cached per slowdown state). A unit that's returning from a call is treated as already back at its post.
- **Calibration:** pick a `city_factor` per hour-of-day bucket so that the baseline policy's sim median matches the actual historical median on the training weeks. Report the calibration error on screen.
- Baseline policy = every unit at its historically most-frequent station area; send the closest idle unit.

**Learned road speeds (A, stretch after the 1:30 checkpoint)**
- Why: the fastest route is already exact once road speeds are known, so better routes come from better speeds. Speed limits times one citywide factor misses patterns like "the Mission at 5pm is 40% slower than posted."
- Signal: actual drive time = `on_scene_dttm − response_dttm` for each training call. Predicted drive time = the route time from the unit's usual post to the scene. SF publishes no GPS tracks, so speeds are learned per area, not per street.
- Model: one speed factor per (neighborhood × hour bucket). Hour buckets: night, morning rush, midday, evening rush, evening. Factor = median of actual ÷ predicted over that cell's calls, shrunk toward the citywide factor when the cell has fewer than 30 calls.
- Applied to: drives into that neighborhood (`route time × bucket factor × neighborhood multiplier`), with camera slowdowns on the route edges.
- Adopt only if it helps: fit on weeks 1 to 3, compare the median absolute error of predicted vs actual drive time on the holdout week, keep the factors only if the error drops, and report both numbers on screen.
- UI: a heatmap layer (neighborhood colored by its factor at the selected hour) next to the camera markers.

**Agent (`agent.py`)**
1. Run the current best on training weeks and collect the summary plus the 50 worst calls, each with context (hour, neighborhood, which unit went, from where, nearest idle unit and its ETA).
2. Prompt Claude (latest Sonnet for speed) with the current policy code, the summary, the worst calls, and the last 5 diagnoses from `versions.jsonl`. It returns a diagnosis plus the full new policy file.
3. Run the candidate in a subprocess with a timeout. Reject it on a crash or a timeout.
4. Gate 1: it must beat the current best on the training weeks. Gate 2: it must also beat the current best on the **holdout week** (week 4). Only then is it promoted. The frozen week (week 5) is evaluated once, for the final headline, with `sim.py --compare baseline` (paired bootstrap CI).
5. Append `{version, parent, diagnosis, train_median, frozen_median, accepted}` to `versions.jsonl`.
6. Run 6 to 10 iterations before the demo, and keep the run log; this becomes the improvement chart.

**Cameras (`cameras.py`)**
- Every 30s per camera, read a 3s clip from its HLS URL with OpenCV.
- Run YOLOv8n with `model.track` on vehicle classes. Measure the vehicle count and `moving_frac` (share of tracks moving more than N px/s).
- `slowdown = clip(1 + k·(1 − moving_frac)·min(vehicles/20, 1), 1, 4)`.
- Apply the slowdown to freeway edges within 600 m of the camera.
- Expose 10 cameras near SF's busiest calls.
- **Record jam clips at the 5pm rush** from Caltrans into `data/clips/` and add a replay mode that feeds a saved clip, so the demo doesn't depend on live traffic.

**UI (`frontend/`)**
- Leaflet map of SF with the call replay.
- Two side-by-side counters: baseline median and agent median for the frozen week.
- Version chart: train and frozen medians per version, with rejected candidates greyed out.
- A diagnosis feed showing the agent's text for each version.
- Camera markers that are green, yellow or red, with a thumbnail on click.
- A "dispatch now" panel showing the chosen unit, the ETA, and the reason when a camera changes the route.

## Timeline

| Person | Owns |
|---|---|
| **A** | Data and simulator: `fetch.py`, `sim.py`, calibration, `edge_slowdowns()` hook |
| **B** | Cameras and UI: `cameras.py`, rush-hour clips, React map, camera overlay |
| **C** | Agent and API: `agent.py`, prompts, `versions.jsonl`, `api.py`, version chart, diagnosis feed, pitch |

Each person runs Claude Code agents in parallel on sub-pieces. C starts before the simulator exists by coding against a stub `replay()` that returns the summary contract with fake numbers.

| Time | A | B | C | Checkpoint |
|---|---|---|---|---|
| 0:00–0:20 | Together: freeze the contracts, repo skeleton, `fetch.py` starts downloading | | | Contracts committed |
| 0:20–1:30 | `fetch.py` done; `sim.py` with baseline policy; calibration | `cameras.py` reads 1 Caltrans HLS stream → YOLO counts and `moving_frac` | `agent.py` loop against the stub: prompt, subprocess runner with timeout, both gates, `versions.jsonl`; FastAPI serving fixture JSON | **1:30 go/no-go:** baseline sim median within 10% of actual, otherwise switch to straight-line × factor |
| 1:30–2:45 | Sim speed-up (cached post Dijkstras), `--self-check`, worst-50 context for the agent; **stretch:** learned road speeds (skip if calibration missed the checkpoint) | 10 cameras live, slowdown formula, React map + replay from fixtures, camera markers | Swap stub for real `sim.py`; first 3 real iterations; tune the prompt on real diagnoses | At least one accepted version beats baseline on the frozen week |
| 2:45–3:45 | Wire `edge_slowdowns()` into sim; with/without-jam test | Record rush-hour jam clips + replay mode; wire UI to real API; "dispatch now" panel | 6–10 iterations in the background; version chart + diagnosis feed; draft pitch with real numbers | Full loop visible in the UI |
| 3:45–4:30 | Freeze the policy version. Rehearse together, fix the flakiest piece, cut whatever isn't working (cameras first) | | | Demo runs 3 times cleanly |
| 4:30–5:00 | README | Backup video | Final pitch | Submitted |

## Task tracker

Tick a box (`- [x]`) and push when a task is done. Each task links back to the timeline above.

### Together
- [x] Freeze contracts (`backend/contracts.py`)
- [x] Repo skeleton, `.gitignore`, `requirements.txt`, README
- [x] `fetch.py` downloading calls, posts, units, cameras, road graph
- [x] Fixtures for B and C (`fixtures/`)
- [x] Frontend scaffold (Vite + React + Leaflet)

### A: data and simulator
- [x] `fetch.py` done, including unit dispatch history for background load
- [x] `sim.py` replay with real road travel times and unit availability
- [x] Baseline policy running in the real simulator
- [x] Calibration: baseline sim median within 10% of actual (1:30 go/no-go): +2.6% train, +6.0% frozen
- [x] Sim speed-up (cached travel times from posts): full train replay in about 1.5s
- [x] `sim.py --self-check` passing
- [x] Simulator hardened against cheating policies (source check, opaque call ids, validated choices, relocation and return-drive costs)
- [x] Holdout week split; paired bootstrap CI via `--compare`
- [x] Worst-50 calls with context for the agent (`sim.py --worst 50`)
- [x] Stretch: learned road speeds, gated on the holdout week: error 287s → 244s, adopted
- [ ] `edge_slowdowns()` wired into the simulator
- [ ] With/without-jam test
- [ ] README

### B: cameras and UI
- [ ] `cameras.py` reads one Caltrans HLS stream → YOLO vehicle count and `moving_frac`
- [ ] 10 cameras live with the slowdown formula
- [ ] React map with call replay from fixtures
- [ ] Camera markers (green/yellow/red, thumbnail on click)
- [ ] Rush-hour jam clips recorded, plus replay mode
- [ ] UI wired to the real API
- [ ] "Dispatch now" panel with reason text
- [ ] Playwright screenshots at 390px and 1440px
- [ ] Backup video

### C: agent and API
- [x] `agent.py` loop: prompt, subprocess runner with timeout, both gates, `versions.jsonl` (tested with `--backend stub`)
- [x] FastAPI serving fixture JSON, falling back from `backend/runs/`
- [x] Swap the stub for the real `sim.py` (agent evaluates with `sim.simulate` directly)
- [ ] First 3 real iterations, prompt tuned
- [ ] At least one version beats baseline on the frozen week
- [ ] 6 to 10 iterations run, final version frozen
- [x] Version chart and diagnosis feed in the UI (`frontend/src/agent/`)
- [ ] Pitch with real numbers

### Checkpoints
- [x] 1:30 go/no-go: calibration within 10%
- [ ] 2:45: an accepted version beats baseline on the frozen week
- [ ] 3:45: full loop visible in the UI
- [ ] 4:30: demo runs 3 times cleanly
- [ ] 5:00: submitted

## Demo script (3 minutes)

1. **Hook (20s):** "In a cardiac arrest, every minute without care cuts survival by about 10%. SF's ambulance median is X minutes."
2. **The loop (60s):** show the version chart. Read one diagnosis aloud, for example "Bayview had no idle unit at 6pm weekdays; repositioning M509." Show the improvement and the rejected candidates that failed the frozen-week gate.
3. **The result (40s):** split-screen replay of a frozen-week Tuesday with the two medians. State the headline number and the calibration error.
4. **Live cameras (40s):** Caltrans feed of a 101 jam (live or a clip recorded at rush hour). The segment turns red. A new call reroutes, and the reason appears on screen.
5. **Close (20s):** what's next: real road speeds, fire engines, integration with dispatch centers.

## Risks and fallbacks

| Risk | Fallback |
|---|---|
| OSM routing is too slow or calibration is off | Straight-line distance × per-hour factor (cameras then scale nearby calls directly) |
| The agent doesn't beat baseline on the frozen week | Seed the prompt with known levers (hourly repositioning, keeping a reserve unit in the busiest areas); report a real but smaller gain honestly |
| A Caltrans HLS stream fails | Poll the 5s JPEG snapshots (count only), or use recorded clips |
| YOLO is slow on the laptop | YOLOv8n at 640px, 3s clips, 10 cameras, sequential |
| Claude writes a broken policy | Subprocess plus timeout plus automatic rejection; it never touches the frozen week |
| Learned speeds don't lower frozen-week error | Keep the per-hour citywide factor and drop the heatmap; nothing else depends on it |

## Verification

- `python backend/fetch.py`: row counts printed, 5 weeks present, frozen week split saved.
- `python backend/sim.py --policy baseline --split train`: prints the median and the calibration error against actual (target within 10%).
- `python backend/sim.py --self-check`: asserts on a 3-call hand-made scenario (closest unit chosen, busy unit skipped, slowdown increases ETA).
- `python backend/agent.py --iters 3`: `versions.jsonl` grows, and at least one version is accepted.
- `python backend/cameras.py --once`: prints a reading for each of 10 cameras with a vehicle count above 0 for daytime feeds.
- `python backend/sim.py --fit-speeds`: prints drive-time error on the frozen week with the citywide factor vs learned factors; learned factors are saved only if the error is lower.
- With/without-jam test: the same calls, with one camera forced to `slowdown=4`; affected calls reroute and ETAs change.
- UI: open the app, watch the replay, click a camera, see the version chart. Take Playwright screenshots at 390px and 1440px.
