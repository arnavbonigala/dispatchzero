SLOW = {
    "Visitacion Valley", "Haight Ashbury", "Excelsior",
    "Oceanview/Merced/Ingleside", "Sunset/Parkside", "Lakeshore",
    "Portola", "Inner Sunset", "Outer Richmond", "Inner Richmond",
}


def post_plan(hour, units):
    return {u: HOME[u] for u in units}


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]


def send_engine(call, engines, eta, ambulance_s):
    try:
        nb = call["nbhd"]
        hr = call["hour"]
    except Exception:
        nb = call.nbhd
        hr = call.hour
    if nb not in SLOW or ambulance_s < 480:
        return None
    best = None
    best_score = None
    for e in engines:
        uid = e["unit_id"]
        load = ENGINE_LOAD.get(uid + "|" + str(hr), 0.0)
        if load > 1.5:
            continue
        s = eta(e, call)
        if s + 120 > ambulance_s:
            continue
        score = s + 150 * load
        if best_score is None or score < best_score:
            best_score = score
            best = uid
    return best
