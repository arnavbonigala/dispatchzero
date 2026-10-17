# Golden Minute: pitch

A 3-minute script with the real numbers, a one-page numbers sheet, a slide outline and answers to the questions judges will ask. Every number comes from `fixtures/engines_headline.json`, `fixtures/engines_agent.json`, `fixtures/engines_baseline.json`, `fixtures/headline.json`, the two `fixtures/*versions.jsonl` run logs, `data/calls.parquet` and the README. Re-check them if the agent is re-run.

## The script (3 minutes)

**1. Hook (20 s)**
> "When someone's heart stops, every minute without help cuts their chance of survival by about 10 percent. In San Francisco, where you live decides how long you wait. In the ten slowest neighborhoods, a typical ambulance takes 12 minutes 45. In the Tenderloin it's 9 minutes 25."

**2. The loop (60 s)** *(scroll to the "How the AI learned it" log, click "Show all 39 steps", find step 8)*
> "Golden Minute is an AI that writes San Francisco's dispatch playbook as code. It replays five weeks of real 911 ambulance calls, studies its slowest responses, and rewrites itself."

Read step 8 (e008) aloud, in the agent's own words:
> "Sunset/Parkside, Lakeshore, Outer Richmond and Oceanview calls wait 800 to 1,100 seconds for downtown ambulances while an idle paramedic engine is usually 200 to 400 seconds away."

> "Every new version has to win twice: on the weeks it studied, and again on a week it never saw. Most don't. 29 of its 39 attempts were thrown out, and they stay in the log, marked rejected with the reason, so you can see it doesn't fool itself."

**3. The result (40 s)** *(replay a frozen-week day on the map and flip the Baseline / Golden Minute toggle; slow neighborhoods are highlighted)*
> "This is a real week of San Francisco calls the AI never saw, replayed both ways. With the AI's playbook the first paramedic arrives a minute and a half sooner on average, and nearly two and a half minutes sooner in the ten slowest neighborhoods. Calls reached within eight minutes go from 37 to 53 percent."

> "The cost: sending paramedic engines more often means 32 more fire-department runs a week find their engine busy, about 38 extra seconds each. The simulator matches SF's real median response time to within half a percent."

**4. Live cameras (40 s)** *(point at the camera markers, open one camera's live image, then click the map to dispatch a call)*
> "It also reads Caltrans's live freeway cameras. Each marker turns red when traffic there slows, and you can open the live image. Click anywhere on the map and it dispatches a call right now: which ambulance and which engine, and why."

If asked: freeway jams rarely touch in-city ambulance routes (most cameras affect 0 to 14 calls a week). The clearest case is SR-1 at the Presidio Tunnel, where about 2 calls a week switch unit and save about 2 minutes each. Present cameras as the live layer, not the source of the headline gain. Only say the dispatch "routes around the jam" once camera-aware routing is merged and shown in the dispatch panel.

**5. Close (20 s)**
> "Golden Minute learns from every call San Francisco has already answered, so the next paramedic gets there sooner. Next: a second opinion for real dispatchers, plus two changes the simulator says are worth more than any positioning trick: dispatching at call entry, and capping hospital handoff times."

## Numbers sheet

**Headline: paramedic engines + ambulances, version e036, frozen week (1,708 calls the agent never saw), first paramedic on scene vs baseline**

| Measure | Baseline | Golden Minute | Change |
|---|---|---|---|
| Average | 606 s (10:06) | 515 s (8:35) | **−91 s** |
| Median | 557 s | 470 s | −86 s (95% CI −101 to −75) |
| 10 slowest neighborhoods, median (287 calls) | 588 s | 442 s | **−146 s** (95% CI −168 to −114) |
| Slowest 10% of calls (90th percentile) | 885 s | 685 s | −199 s |
| Reached within 8 minutes | 37% | 53% | +17 points |
| Engine sends per week | 722 | 1,219 | +497 |
| Real fire-department runs whose engine was busy (per week) | 24 | 56 | +32 |
| Extra drive for those runs from the next engine over (per week) | 1,030 s | 2,240 s | +1,210 s, about 38 s per extra conflict |

"Fire-department runs" means every real engine dispatch that isn't one of the simulated ambulance calls: fires and other engine work. The engine agent rejects candidates with more than twice the baseline's conflicts on the training or holdout weeks. On the frozen week e036 came in at 56 vs 24, a little over that line. The cost is real, and the pitch reports it.

**Ambulance positioning only (no engines), version v013, frozen week**

| Measure | Change vs baseline |
|---|---|
| 10 slowest neighborhoods, median | −14 s (95% CI −36 to −5) |
| Citywide median | +2.7 s (95% CI −2.7 to +7.6), inside the 5 s budget |

This is the original goal (faster slow neighborhoods, nobody else slower), and it passes. The gain is small because SF rarely runs out of ambulances. Repositioning can only shave drive time, and the baseline already spreads units by call volume. Paramedic engines are where the big gain is.

**The gap (real history, 5 weeks, call received to first ambulance on scene, median)**
- 10 slowest neighborhoods pooled: 765 s (12:45). Visitacion Valley 927 s, Excelsior 805 s, Sunset/Parkside 760 s.
- Tenderloin: 565 s (9:25). Downtown pooled (Tenderloin, SoMa, Financial District, Chinatown, Nob Hill): 630 s. Citywide: 662 s.

**How the agent got there**
- Ambulance loop: 13 candidates, 5 accepted (v007, v008, v011, v012, v013).
- Engine loop: 39 candidates, 10 accepted (e008 to e036). Of the 29 rejected, most failed the training gate (not better than the current best, or too many engine conflicts). 5 failed the unseen holdout week, 3 were blocked by the code checker, and 2 were beaten by a better candidate in the same round.
- Gate: beat the current best on the training weeks (weeks 1 to 3), then pass on the holdout week (week 4). The frozen week (week 5) is never used to accept or reject anything and is never shown to the agent. Accepted versions are scored on it afterward for the record, and the headline is the latest accepted version.

**Trust**
- Calibration: simulated median 660 s vs SF's real 658 s on the frozen week (+0.3%). First paramedic: 557 s vs 560 s.
- Agent code is checked before it runs (whitelisted imports only, no file access). It then runs in a sandbox with no network and no credentials. It only sees fields a dispatcher would see, and the simulator computes its score by replaying its decisions, so the code can't grade itself.

**Policy what-ifs (holdout week, not agent results)**
- Dispatch at call entry (for example an AI pre-alert): everyone −62 s at the median (upper bound).
- Cap hospital handoff at 20 minutes: everyone −20 s. Combined with dispatch at entry: −80 s.

## Slides (5)

1. **Title.** Golden Minute: an AI that rewrites San Francisco's ambulance playbook. One line: "A minute and a half sooner on average, on a week it never saw."
2. **The problem.** Survival falls about 10% per minute. The 10 slowest neighborhoods wait 12:45 for an ambulance; the Tenderloin waits 9:25. Map of neighborhood medians.
3. **How it works.** Real calls → simulator calibrated to SF (within 0.5%) → Claude writes a policy → training gate → unseen-week gate → keep or throw out. Show the learning log with its rejected steps.
4. **The result.** The numbers sheet's first table: −91 s average, −146 s in the slowest neighborhoods, 37% → 53% within 8 minutes, with the fire-department cost next to it.
5. **What's next.** Second opinion for real dispatchers; dispatch at call entry (−62 s); hospital handoff cap (−20 s); live traffic.

## Questions to expect

- **"Are these real-world seconds?"** No. They are simulator-relative estimates, from a simulator calibrated to SF's real medians (within 0.5%). SF publishes no unit locations, so per-call drive times match only loosely. Medians match; individual calls don't.
- **"Did it just memorize the data?"** Every accepted version had to pass a holdout week it never saw. The headline comes from a third week, the frozen week, which is never used to accept or reject anything and never shown to the AI. 37 of the 52 candidates across both loops were rejected.
- **"Couldn't the AI cheat?"** Its code is checked before it runs (no file access, whitelisted imports), runs sandboxed with no network or credentials, and only sees dispatcher-visible call fields. Its score comes from the simulator replaying its decisions, not from its own code.
- **"Who pays for sending engines more?"** The fire department: 32 more runs a week find their engine busy, adding about 38 s each for the next engine over. Candidates over twice the baseline's conflicts on training or holdout are rejected. On the frozen week the final version came in slightly over that line (56 vs 24), and we show it.
- **"Why so small without engines?"** SF rarely runs out of free ambulances (about 3 queued calls a week), so positioning only trims drive time. The −14 s in the slow neighborhoods is real (CI below zero) but modest.
- **"What do the cameras add?"** A live traffic signal and the demo moment. Measured weekly impact is small because ambulances mostly stay off the freeways the cameras cover.

Before presenting, confirm the survival statistic with a citable source (American Heart Association guidance on time to CPR and defibrillation). See `NON_CODING_TASKS.md`.
