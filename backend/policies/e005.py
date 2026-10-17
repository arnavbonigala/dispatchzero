def post_plan(hour, units):
    return {u: HOME[u] for u in units}


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]


def send_engine(call, engines, eta, ambulance_s):
    best = None
    best_s = ambulance_s - 45
    for e in engines:
        uid = e["unit_id"]
        if ENGINE_LOAD.get(uid + "|" + str(call["hour"]), 0) > 1.0:
            continue
        s = eta(e, call)
        if s < best_s:
            best_s = s
            best = uid
    return best
