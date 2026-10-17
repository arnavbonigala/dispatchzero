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

TARGETS = [2, 0, 33, 7]


def post_plan(hour, units):
    plan = {u: HOME[u] for u in units}
    if hour < 10 or hour > 15:
        return plan
    count = {}
    for u in units:
        count[HOME[u]] = count.get(HOME[u], 0) + 1
    moved = 0
    for t in TARGETS:
        if moved >= 2:
            break
        if count.get(t, 0) > 0:
            continue
        best = None
        best_d = None
        for p in count:
            if p in TARGETS or count[p] < 2:
                continue
            try:
                d = POST_DRIVE[p][t]
            except Exception:
                continue
            if d > 600:
                continue
            if best_d is None or d < best_d:
                best = p
                best_d = d
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
