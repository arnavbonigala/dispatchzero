def post_plan(hour, units):
    plan = {u: HOME[u] for u in units}
    if len(units) < 20:
        return plan
    count = {}
    for u in sorted(units):
        count[HOME[u]] = count.get(HOME[u], 0) + 1
    for target in (22, 40):
        if count.get(target, 0) >= 2:
            continue
        donors = [p for p in count if count[p] >= 4 and p not in (22, 40, 31)]
        if not donors:
            break
        donors.sort(key=lambda p: (-count[p], p))
        d = donors[0]
        cand = [u for u in sorted(units) if HOME[u] == d]
        plan[cand[-1]] = target
        count[d] -= 1
        count[target] = count.get(target, 0) + 1
    return plan


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]
