def _d(a, b):
    if a == b:
        return 0.0
    return float(POST_DRIVE[a][b])


def _weights(bucket):
    dem = DEMAND.get(bucket, {})
    w = {}
    for p in POSTS:
        k = p["post"]
        base = dem.get(k, 0) + 0.5
        if p["lon"] < -122.45 or p["lat"] < 37.73:
            base *= 1.5
        w[k] = base
    return w


def _layout(bucket, n):
    w = _weights(bucket)
    keys = list(w.keys())
    best = {k: 1e9 for k in keys}
    chosen = []
    for _ in range(n):
        top = None
        top_cost = None
        for s in keys:
            c = 0.0
            for k in keys:
                dd = min(best[k], _d(s, k))
                c += w[k] * dd ** 1.5
            if top_cost is None or c < top_cost:
                top_cost = c
                top = s
        chosen.append(top)
        for k in keys:
            best[k] = min(best[k], _d(top, k))
    return chosen


def post_plan(hour, units):
    units = list(units)
    if not units:
        return {}
    targets = _layout(BUCKET_OF[hour], len(units))
    pairs = []
    for u in units:
        for i, t in enumerate(targets):
            pairs.append((_d(HOME[u], t), u, i))
    pairs.sort()
    plan = {}
    used = set()
    for c, u, i in pairs:
        if u in plan or i in used:
            continue
        plan[u] = targets[i]
        used.add(i)
    for u in units:
        if u not in plan:
            plan[u] = HOME[u]
    return plan


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]
