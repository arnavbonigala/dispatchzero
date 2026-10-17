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


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]


def _get(call, k):
    try:
        return getattr(call, k)
    except Exception:
        return call[k]


def send_engine(call, engines, eta, ambulance_s):
    h = _get(call, "hour")
    n = _get(call, "nbhd")
    lt = _get(call, "life_threatening")
    if n in SLOW:
        min_amb = 350
        margin = 60
        cap = 0.7
    else:
        margin = 85
        cap = 0.45
        if 10 <= h <= 15:
            min_amb = 420
        else:
            min_amb = 440
        if lt:
            min_amb -= 25
        else:
            min_amb += 25
    if ambulance_s < min_amb:
        return None
    cands = []
    for e in engines:
        uid = e["unit_id"]
        load = ENGINE_LOAD.get(uid + "|" + str(h), 0.0)
        if load > cap:
            continue
        s = eta(e, call)
        if s + margin > ambulance_s:
            continue
        cands.append((s, load, uid))
    if not cands:
        return None
    fastest = min(c[0] for c in cands)
    near = [c for c in cands if c[0] <= fastest + 20]
    near.sort(key=lambda c: (c[1], c[0]))
    return near[0][2]
