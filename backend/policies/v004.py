RESERVE = {2: 3, 0: 3}
TARGETS = [22, 40, 31, 18, 41, 36, 30, 28, 14, 13, 9]
MAX_MOVES = 6
MIN_MOVES = 4
PENALTY = 40.0
SLOW = {
    "Excelsior", "Haight Ashbury", "Inner Richmond", "Lakeshore",
    "Oceanview/Merced/Ingleside", "Outer Richmond", "Portola",
    "Sunset/Parkside", "Visitacion Valley", "West of Twin Peaks",
}
TARGET_SET = set(TARGETS)


def post_plan(hour, units):
    plan = {u: HOME[u] for u in units}
    occ = {}
    for u in units:
        p = HOME[u]
        occ[p] = occ.get(p, 0) + 1
    extras = []
    for p in (2, 0):
        at = sorted([u for u in units if HOME[u] == p])
        extras.extend(at[RESERVE[p]:])
    limit = MAX_MOVES if len(units) >= 20 else MIN_MOVES
    extras = extras[:limit]
    for u in extras:
        best = None
        bestkey = None
        for i, t in enumerate(TARGETS):
            key = (occ.get(t, 0), i)
            if bestkey is None or key < bestkey:
                bestkey = key
                best = t
        plan[u] = best
        occ[best] = occ.get(best, 0) + 1
        occ[HOME[u]] = occ.get(HOME[u], 0) - 1
    return plan


def choose_unit(call, idle, eta):
    slow_call = call.get("nbhd") in SLOW
    best = None
    bestkey = None
    for u in idle:
        e = eta(u, call)
        if not slow_call and u["post"] in TARGET_SET:
            e = e + PENALTY
        key = (e, u["unit_id"])
        if bestkey is None or key < bestkey:
            bestkey = key
            best = u
    return best["unit_id"]
