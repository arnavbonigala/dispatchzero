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
TARGETS = [22, 18, 13]


def post_plan(hour, units):
    plan = {u: HOME[u] for u in units}
    if BUCKET_OF[hour] == "midday":
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
        for p in DOWNTOWN:
            if count.get(p, 0) > 2 and (best is None or count[p] > count[best]):
                best = p
        if best is None:
            break
        for u in sorted(units):
            if HOME[u] == best and plan[u] == best:
                plan[u] = t
                count[best] -= 1
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
        cap = 0.4
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
