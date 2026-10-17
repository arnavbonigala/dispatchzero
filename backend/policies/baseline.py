def post_plan(hour, units):
    return {u: HOME[u] for u in units}


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]
