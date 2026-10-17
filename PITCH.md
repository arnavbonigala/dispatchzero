# Golden Minute: pitch

A 3-minute script with the real numbers, a one-page numbers sheet, a slide outline and answers to the questions judges will ask. Every number comes from `fixtures/engines_headline.json`, `fixtures/headline.json`, the two `*versions.jsonl` run logs and the README. Re-check them if the agent is re-run.

## The script (3 minutes)

**1. Hook (20 s)**
> "When someone's heart stops, every minute without help cuts their chance of survival by about 10 percent. In San Francisco, where you live decides how long you wait: the slowest neighborhoods, like Visitacion Valley, the Excelsior and the Sunset, wait about three and a half minutes longer than downtown."

**2. The loop (60 s)** *(show the version chart)*
> "Golden Minute is an AI that writes San Francisco's dispatch playbook as code. It replays five weeks of real 911 ambulance calls, studies its slowest responses, and rewrites itself."

Read one diagnosis aloud, in the agent's own words (e008):
> "Sunset/Parkside, Lakeshore, Outer Richmond and Oceanview calls wait 800 to 1,100 seconds for downtown ambulances while an idle paramedic engine is usually 200 to 400 seconds away."

> "Every new version has to win twice: on the weeks it studied, and again on a week it never saw. Most don't. 29 of its 39 attempts were thrown out, and they stay on the chart, grey, so you can see it doesn't fool itself."

**3. The result (40 s)** *(split-screen replay of a frozen-week day, slow neighborhoods highlighted)*
> "Here's a real week of San Francisco calls the AI never saw, replayed both ways in the same simulator. With the AI's playbook, the first paramedic arrives a minute and a half sooner on average, and almost two and a half minutes sooner in the ten slowest neighborhoods. Calls reached within eight minutes go from 37 to 53 percent."

> "And it's honest about the cost: sending paramedic engines more often means 32 more times a week when one is busy and a real fire call goes to the next engine over. That costs those fire calls about 40 seconds each."

> "The simulator is calibrated to SF's real response times: within 1 percent on the median."

**4. Live cameras (40 s)** *(Caltrans feed, jam turns the segment red, click the map to dispatch)*
> "It also watches Caltrans's live freeway cameras. When a camera shows a jam, the road turns red and the next dispatch routes around it, with the reason on screen."

Be ready for the follow-up: freeway jams rarely touch in-city ambulance routes (most cameras affect 0 to 14 calls a week). The clearest case is SR-1 at the Presidio Tunnel: about 2 calls a week switch unit, saving about 2 minutes each. Present the cameras as the live layer, not as the source of the headline gain.

**5. Close (20 s)**
> "Golden Minute learns from every call San Francisco has already answered, so the next paramedic gets there sooner. Next: a second opinion for real dispatchers, and two policy changes the simulator says are worth more than any positioning trick: dispatching at call entry, and capping hospital handoff times."

## Numbers sheet

**Headline: paramedic engines + ambulances, version e036, frozen week (1,708 calls the agent never saw), first paramedic on scene vs baseline**

| Measure | Baseline | Golden Minute | Change |
|---|---|---|---|
| Average | 606 s (10:06) | 515 s (8:35) | **−91 s** |
| Median | 557 s | 470 s | −86 s (95% CI −101 to −75) |
| 10 slowest neighborhoods, median (287 calls) | 588 s | 442 s | **−146 s** (95% CI −168 to −114) |
| Slowest 10% of calls (90th percentile) | 885 s | 685 s | −199 s |
| Reached within 8 minutes | 37% | 53% | +16 points |
| Engine sends per week | 722 | 1,219 | +497 |
| Fire calls whose engine was busy (per week) | 24 | 56 | +32 |
| Extra drive for those fire calls (per week) | 1,030 s | 2,240 s | +1,210 s total, about 38 s per extra conflict |

**Ambulance positioning only (no engines), version v013, frozen week**

| Measure | Change vs baseline |
|---|---|
| 10 slowest neighborhoods, median | −14 s (95% CI −36 to −5) |
| Citywide median | +2.7 s (95% CI −2.7 to +7.6), inside the 5 s budget |

This is the original goal (faster slow neighborhoods, nobody else slower), and it passes. It is small because SF rarely runs out of ambulances. Repositioning can only shave drive time, and the baseline already spreads units by call volume. Paramedic engines are where the big gain is.

**How the agent got there**
- Ambulance loop: 13 candidates, 5 accepted (v007, v008, v011, v012, v013).
- Engine loop: 39 candidates, 10 accepted (e008 to e036). Rejections include candidates that failed the unseen holdout week, used too many engines, or were beaten by a better candidate in the same round.
- Gate: beat the current best on the training weeks (weeks 1 to 3), then pass on the holdout week (week 4). The frozen week (week 5) is used only for the headline, never for gating.

**Trust**
- Calibration: simulated median 664 s vs SF's real 658 s on the frozen week (+0.9%); first paramedic 557 s vs 560 s.
- Agent-written code runs in a sandbox and is scored by replaying its decisions through the simulator, so it can't see the frozen week or fake its score.

**Policy what-ifs (holdout week, not agent results)**
- Dispatch at call entry (for example an AI pre-alert): everyone −62 s at the median (upper bound).
- Cap hospital handoff at 20 minutes: everyone −20 s; with dispatch at entry, −80 s.

## Slides (5)

1. **Title.** Golden Minute: an AI that rewrites San Francisco's ambulance playbook. One line: "A minute and a half sooner on average, on a week it never saw."
2. **The problem.** Survival falls about 10% per minute. The slowest neighborhoods wait about 3.5 minutes longer than downtown. Map of neighborhood medians.
3. **How it works.** Real calls → simulator calibrated to SF (within 1%) → Claude writes a policy → train gate → unseen-week gate → keep or throw out. Show the version chart with grey rejects.
4. **The result.** The numbers sheet's first table: −91 s average, −146 s in the slowest neighborhoods, 37% → 53% within 8 minutes, with the fire-call cost next to it.
5. **What's next.** Second opinion for real dispatchers; dispatch at call entry (−62 s); hospital handoff cap (−20 s); live traffic speeds.

## Questions to expect

- **"Is this real-world seconds?"** No. These are simulator-relative estimates in a simulator calibrated to SF's real medians (within 1%). SF publishes no unit locations, so per-call drive times match only loosely. Medians match, individual calls don't.
- **"Did it just memorize the data?"** Every accepted version had to pass a holdout week it never saw, and the headline is a third, frozen week touched once. 37 of the 52 candidates across both loops were rejected.
- **"Couldn't the AI cheat?"** Its code runs in a sandbox with no file, network or credential access. It only sees dispatcher-visible call fields, and its score is computed by replaying its decisions, not by its own code.
- **"Who pays for sending engines more?"** Fire calls: 32 more a week find their engine busy, adding about 38 s each for the next engine over. The engine agent is gated on that conflict count.
- **"Why so small without engines?"** SF rarely runs out of free ambulances (about 3 queued calls a week), so positioning only trims drive time. The −14 s in the slow neighborhoods is real (CI below zero) but modest.
- **"What do the cameras add?"** A live routing layer and the demo moment. Measured weekly impact is small because ambulances mostly stay off the freeways the cameras cover.

Before presenting, confirm the survival statistic with a citable source (American Heart Association guidance on time to CPR and defibrillation). See `NON_CODING_TASKS.md`.
