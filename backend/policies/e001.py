def post_plan(hour, units):
    plan = {u: HOME[u] for u in units}
    down = [0, 2, 7]
    targets = [22, 18, 13]
    occ = {}
    for u in units:
        occ.setdefault(HOME[u], []).append(u)
    targets = sorted(targets, key=lambda p: len(occ.get(p, [])))
    moved = 0
    for p in sorted(down, key=lambda p: -len(occ.get(p, []))):
        spare = sorted(occ.get(p, []))
        while len(spare) > 2 and moved < 3:
            u = spare.pop()
            plan[u] = targets[moved]
            moved += 1
    return plan


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]
