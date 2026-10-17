def post_plan(hour, units):
    return {u: HOME[u] for u in units}


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]


def send_engine(call, engines, eta, ambulance_s):
    hour = call["hour"]
    best = None
    best_gain = 90.0
    for e in engines:
        uid = e["unit_id"]
        load = ENGINE_LOAD.get(uid + "|" + str(hour), 0.0)
        if load > 0.6:
            continue
        gain = ambulance_s - eta(e, call)
        if gain >= best_gain:
            best_gain = gain
            best = uid
    return best
