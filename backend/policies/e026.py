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


def post_plan(hour, units):
    return {u: HOME[u] for u in units}


def _get(c, k):
    try:
        return c[k]
    except Exception:
        return getattr(c, k)


def choose_unit(call, idle, eta):
    if len(idle) < 3:
        return min(idle, key=lambda u: eta(u, call))["unit_id"]
    try:
        lt = _get(call, "life_threatening")
    except Exception:
        lt = True
    try:
        nb = _get(call, "nbhd")
    except Exception:
        nb = ""
    if lt or nb in SLOW:
        return min(idle, key=lambda u: eta(u, call))["unit_id"]
    try:
        d = DEMAND.get(BUCKET_OF[_get(call, "hour")], {})
    except Exception:
        d = {}
    if not d:
        return min(idle, key=lambda u: eta(u, call))["unit_id"]
    mx = max(d.values())
    if mx <= 0:
        return min(idle, key=lambda u: eta(u, call))["unit_id"]

    def score(u):
        p = None
        try:
            p = u["post"]
        except Exception:
            p = None
        pen = 0.0
        if p is not None:
            pen = 45.0 * d.get(p, 0) / mx
        return eta(u, call) + pen

    return min(idle, key=score)["unit_id"]


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
