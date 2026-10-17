def post_plan(hour, units):
    return {u: HOME[u] for u in units}


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]


def send_engine(call, engines, eta, ambulance_s):
    if not engines:
        return None
    score = lambda u: ambulance_s - eta(u, call) - 600 * ENGINE_LOAD.get(f"{u['unit_id']}|{call['hour']}", 0)
    best = max(engines, key=score)
    return best["unit_id"] if score(best) > 30 else None
