# Golden Minute

## The problem

When someone's heart stops, every minute without help lowers their chance of survival by roughly 7 to 10 percent. How fast an ambulance arrives often comes down to decisions made long before the 911 call: where the city's ambulances are waiting, and which one gets sent.

Those decisions mostly follow fixed habits. Ambulances return to the same spots, and the closest one on the map gets sent, even when traffic says otherwise. Nobody has time to study thousands of past calls and work out what would have been faster.

## The idea

Golden Minute is an AI dispatcher that studies San Francisco's real ambulance history and teaches itself to get there faster.

1. **It learns from real calls.** San Francisco publishes every emergency call: where it happened, which ambulance went, and how long it took to arrive. Golden Minute replays weeks of those calls.
2. **It writes its own playbook.** The AI writes rules for where ambulances should wait at each hour of the day and which one to send to each call.
3. **It studies its worst mistakes.** After each replay, it looks at its slowest responses and figures out why. For example: "The southeast of the city had no free ambulance at 6pm on weekdays." Then it rewrites its playbook.
4. **It has to prove every improvement.** A new playbook only counts if it also does better on a week of calls the AI has never seen. That keeps it honest: it can't just memorize the answers.
5. **It watches the roads.** Golden Minute reads Caltrans's live traffic cameras on San Francisco freeways. When a camera shows a jam, it sends a different ambulance or takes a different route, and it explains why.

## Why it matters

- **Real data, real city.** Every call in the demo actually happened in San Francisco.
- **A number you can check.** The result is measured in seconds saved per call on calls the AI never trained on.
- **It explains itself.** Every decision comes with a plain-language reason a dispatcher can read and question.

## The demo

**Scene 1: The playbook gets better (1 minute)**
A chart shows each version of the AI's playbook getting faster. We read one of its notes out loud, in its own words, explaining what it fixed. Some attempts failed the "never-seen week" test and were thrown out. Those stay on the chart too, showing that it doesn't fool itself.

**Scene 2: A real Tuesday, replayed (45 seconds)**
A map of San Francisco splits in two. On the left, ambulances respond the usual way. On the right, they follow Golden Minute's playbook. The same real 911 calls appear on both sides, and a clock under each side shows the typical arrival time. The right side is faster by a clear number of seconds.

**Scene 3: A traffic jam (45 seconds)**
A live Caltrans camera, or one we recorded during rush hour, shows Highway 101 at a standstill. That stretch of road turns red on the map. The next emergency call comes in, and Golden Minute sends a different ambulance on a clear route. A note appears on screen: "Highway 101 is jammed near this camera. Sending the unit from the other side, arriving 2 minutes sooner."

**Closing line:**
*"Golden Minute learns from every call San Francisco has already answered, so the next ambulance gets there sooner."*

## What's next

Real-time road speeds across the whole city, fire engines alongside ambulances, and a pilot with a real dispatch center as a second opinion for human dispatchers.
