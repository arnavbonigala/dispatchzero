SLOW = {
    "Visitacion Valley",
    "Haight Ashbury",
    "Excelsior",
    "Oceanview/Merced/Ingleside",
    "Sunset/Parkside",
    "Lakeshore",
    "Portola",
    "Inner Sunset",
    "Outer Richmond",
    "Inner Richmond",
}

DOWNTOWN = [0, 2, 7]


def _g(d, k, default=0):
    if k in d:
        return d[k]
    if str(k) in d:
        return d[str(k)]
    return default


def post_plan(hour, units):
    plan = {u: HOME[u] for u in units}
    if BUCKET_OF[hour] != "midday":
        return plan
    dem = DEMAND.get("midday", {})
    count = {}
    for u in units:
        count[HOME[u]] = count.get(HOME[u], 0) + 1
    moved = 0
    for t in sorted(DOWNTOWN, key=lambda p: -_g(dem, p)):
        if moved >= 3:
            break
        if count.get(t, 0) > 0:
            continue
        row = _g(POST_DRIVE, t, {})
        best = None
        best_score = None
        for p in count:
            if p in DOWNTOWN or count[p] < 2:
                continue
            d = _g(_g(POST_DRIVE, p, {}), t, 99999)
            if d > 600:
                continue
            score = _g(dem, p) / count[p]
            if best is None or score < best_score:
                best = p
                best_score = score
        if best is None:
            continue
        for u in sorted(units):
            if HOME[u] == best and plan[u] == best:
                plan[u] = t
                count[best] -= 1
                count[t] = 1
                moved += 1
                break
    return plan


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]


def send_engine(call, engines, eta, ambulance_s):
    try:
        h = call.hour
    except Exception:
        h = call["hour"]
    try:
        n = call.nbhd
    except Exception:
        n = call["nbhd"]
    if n in SLOW:
        min_amb = 350
        margin = 60
        cap = 0.7
    else:
        min_amb = 440
        margin = 85
        cap = 0.45
        if 10 <= h <= 15:
            min_amb = 420
    if ambulance_s < min_amb:
        return None
    best = None
    best_s = None
    for e in engines:
        uid = e["unit_id"]
        load = ENGINE_LOAD.get(uid + "|" + str(h), 0.0)
        if load > cap:
            continue
        s = eta(e, call)
        if s + margin > ambulance_s:
            continue
        if best_s is None or s < best_s:
            best = uid
            best_s = s
    return best
