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
    try:
        lt = call.life_threatening
    except Exception:
        lt = call["life_threatening"]
    if lt or len(idle) < 2:
        return closest["unit_id"]
    try:
        counts = {}
        for u in idle:
            p = u.get("post")
            counts[p] = counts.get(p, 0) + 1
        limit = etas[closest["unit_id"]] + 60
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
    slow = n in SLOW
    if slow:
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
    best_score = None
    for e in engines:
        uid = e["unit_id"]
        load = ENGINE_LOAD.get(uid + "|" + str(h), 0.0)
        s = eta(e, call)
        gain = ambulance_s - s
        need = margin
        if not slow and load > 0.25:
            need = margin + 40
        if gain < need:
            continue
        if load > cap:
            if slow or gain < 300 or load > cap + 0.2:
                continue
        score = s + 120.0 * load
        if best_score is None or score < best_score:
            best = uid
            best_score = score
    return best
