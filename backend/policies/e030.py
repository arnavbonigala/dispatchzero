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
    etas = {}
    for u in idle:
        etas[u["unit_id"]] = eta(u, call)
    closest = min(idle, key=lambda u: etas[u["unit_id"]])
    if len(idle) < 2:
        return closest["unit_id"]
    try:
        lt = call.life_threatening
    except Exception:
        lt = call["life_threatening"]
    try:
        counts = {}
        for u in idle:
            p = u.get("post")
            counts[p] = counts.get(p, 0) + 1
        window = 20 if lt else 60
        limit = etas[closest["unit_id"]] + window
        best = closest
        best_key = None
        for u in idle:
            s = etas[u["unit_id"]]
            if s > limit:
                continue
            key = (-counts.get(u.get("post"), 0), s)
            if best_key is None or key < best_key:
                best = u
                best_key = key
        return best["unit_id"]
    except Exception:
        return closest["unit_id"]


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
