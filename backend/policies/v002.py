def post_plan(hour, units):
    plan = {u: HOME[u] for u in units}
    counts = {}
    for u in units:
        counts[HOME[u]] = counts.get(HOME[u], 0) + 1
    big = sorted(counts, key=lambda p: -counts[p])[:2]
    targets = [22, 40, 31]
    moved = 0
    for u in sorted(units):
        if moved >= 3:
            break
        p = HOME[u]
        if p in big and counts[p] >= 3:
            plan[u] = targets[moved]
            counts[p] -= 1
            moved += 1
    return plan


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]
