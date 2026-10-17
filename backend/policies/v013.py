def post_plan(hour, units):
    plan = {u: HOME[u] for u in units}
    if len(units) < 15:
        return plan
    counts = {}
    for u in units:
        counts[HOME[u]] = counts.get(HOME[u], 0) + 1
    dem = DEMAND.get(BUCKET_OF[hour], {})
    empty = [p for p in (22, 40, 31, 41, 37) if counts.get(p, 0) == 0]
    empty.sort(key=lambda p: -dem.get(p, 0))
    limit = 4 if len(units) >= 20 else 3
    for t in empty[:limit]:
        best = None
        for u in sorted(units):
            p = plan[u]
            if p != HOME[u] or counts.get(p, 0) < 3:
                continue
            d = POST_DRIVE[p][t]
            if best is None or d < best[0]:
                best = (d, u)
        if best is None:
            break
        u = best[1]
        counts[plan[u]] -= 1
        plan[u] = t
        counts[t] = 1
    return plan


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]
