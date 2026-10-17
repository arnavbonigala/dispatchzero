def post_plan(hour, units):
    return {u: HOME[u] for u in units}


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]


def send_engine(call, engines, eta, ambulance_s):
    if ambulance_s < 500:
        return None
    try:
        h = call.hour
    except Exception:
        h = call["hour"]
    best = None
    best_s = None
    for e in engines:
        uid = e["unit_id"]
        load = ENGINE_LOAD.get(uid + "|" + str(h), 0.0)
        if load > 0.4:
            continue
        s = eta(e, call)
        if s + 100 > ambulance_s:
            continue
        if best_s is None or s < best_s:
            best = uid
            best_s = s
    return best
