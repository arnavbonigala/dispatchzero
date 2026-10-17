LAST = {}
PLAN_CACHE = {}

SLOW_POSTS = set([9, 10, 11, 13, 14, 16, 17, 18, 20, 21, 22, 28, 30, 31, 36, 37, 40, 41])
SLOW_NBHD = set(["Excelsior", "Haight Ashbury", "Inner Richmond", "Lakeshore",
                 "Oceanview/Merced/Ingleside", "Outer Richmond", "Portola",
                 "Sunset/Parkside", "Visitacion Valley", "West of Twin Peaks"])
MULT = 1.7


def target_posts(hour, n):
    bucket = BUCKET_OF[hour]
    key = (bucket, n)
    if key in PLAN_CACHE:
        return PLAN_CACHE[key]
    npost = len(POSTS)
    dem = DEMAND.get(bucket, {})
    w = [0.0] * npost
    for k in dem:
        p = int(k)
        w[p] += float(dem[k])
    for p in range(npost):
        w[p] += 0.3  # smoothing so every area has some weight
        if p in SLOW_POSTS:
            w[p] *= MULT
    cur = [1500.0] * npost
    chosen = []
    for i in range(n):
        best = None
        bestc = None
        for c in range(npost):
            tot = 0.0
            for d in range(npost):
                dd = POST_DRIVE[c][d]
                if dd < cur[d]:
                    tot += w[d] * dd
                else:
                    tot += w[d] * cur[d]
            if bestc is None or tot < bestc:
                bestc = tot
                best = c
        chosen.append(best)
        for d in range(npost):
            dd = POST_DRIVE[best][d]
            if dd < cur[d]:
                cur[d] = dd
    PLAN_CACHE[key] = chosen
    return chosen


def post_plan(hour, units):
    us = sorted(units)
    n = len(us)
    if n == 0:
        return {}
    targets = list(target_posts(hour, n))
    counts = {}
    for t in targets:
        counts[t] = counts.get(t, 0) + 1
    result = {}
    remaining = []
    for u in us:
        cp = LAST.get(u, HOME[u])
        if counts.get(cp, 0) > 0:
            counts[cp] -= 1
            result[u] = cp
        else:
            remaining.append(u)
    open_t = []
    for t in sorted(counts):
        for i in range(counts[t]):
            open_t.append(t)
    pairs = []
    for u in remaining:
        cp = LAST.get(u, HOME[u])
        for j in range(len(open_t)):
            pairs.append((POST_DRIVE[cp][open_t[j]], u, j))
    pairs.sort()
    used_u = set()
    used_j = set()
    for d, u, j in pairs:
        if u in used_u or j in used_j:
            continue
        used_u.add(u)
        used_j.add(j)
        result[u] = open_t[j]
    for u in us:
        if u not in result:
            result[u] = LAST.get(u, HOME[u])
        LAST[u] = result[u]
    return result


def choose_unit(call, idle, eta):
    slow_call = call.get("nbhd") in SLOW_NBHD
    best = None
    bests = None
    for u in idle:
        s = eta(u, call)
        if not slow_call and u["post"] in SLOW_POSTS:
            s += 40.0
        if bests is None or s < bests:
            bests = s
            best = u
    return best["unit_id"]
