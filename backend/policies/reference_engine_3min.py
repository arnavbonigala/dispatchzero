def post_plan(hour, units):
    return {u: HOME[u] for u in units}


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]


def send_engine(call, engines, eta, ambulance_s):
    if not engines:
        return None
    best = min(engines, key=lambda u: eta(u, call))
    return best["unit_id"] if eta(best, call) < ambulance_s - 180 else None
