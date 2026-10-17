def post_plan(hour, units):
    plan = {u: HOME[u] for u in units}
    n = len(units)
    if n < 20:
        return plan
    targets = [22, 40]
    if n >= 26:
        targets.append(31)
    on_post = {}
    for u in units:
        on_post.setdefault(HOME[u], []).append(u)
    targets = [t for t in targets if t not in on_post]
    for t in targets:
        donors = [p for p in on_post if len(on_post[p]) >= 3 and p not in (22, 40, 31)]
        if not donors:
            break
        p = max(donors, key=lambda q: (len(on_post[q]), -q))
        u = sorted(on_post[p])[-1]
        on_post[p].remove(u)
        plan[u] = t
    return plan


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]
