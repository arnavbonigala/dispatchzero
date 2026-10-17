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


def _field(call, name):
    try:
        return getattr(call, name)
    except Exception:
        return call[name]


def send_engine(call, engines, eta, ambulance_s):
    h = _field(call, "hour")
    try:
        slow = _field(call, "nbhd") in SLOW
    except Exception:
        slow = False
    if slow:
        min_amb, margin, max_load = 420, 80, 0.5
    else:
        min_amb, margin, max_load = 500, 100, 0.4
    if ambulance_s < min_amb:
        return None
    best = None
    best_s = None
    for e in engines:
        uid = e["unit_id"]
        if ENGINE_LOAD.get(uid + "|" + str(h), 0.0) > max_load:
            continue
        s = eta(e, call)
        if s + margin > ambulance_s:
            continue
        if best_s is None or s < best_s:
            best = uid
            best_s = s
    return best
