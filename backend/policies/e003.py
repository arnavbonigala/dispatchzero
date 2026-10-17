DOWNTOWN = [0, 2, 7]
TARGETS = [22, 18]


def post_plan(hour, units):
    plan = {u: HOME[u] for u in units}
    if BUCKET_OF[hour] != "night":
        return plan
    count = {}
    for u in units:
        count[HOME[u]] = count.get(HOME[u], 0) + 1
    for t in TARGETS:
        best = None
        for p in DOWNTOWN:
            if count.get(p, 0) > 2 and (best is None or count[p] > count[best]):
                best = p
        if best is None:
            break
        for u in sorted(units):
            if HOME[u] == best and plan[u] == best:
                plan[u] = t
                count[best] -= 1
                break
    return plan


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]
