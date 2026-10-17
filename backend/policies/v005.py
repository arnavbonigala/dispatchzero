TARGETS = [22, 40, 31]


def post_plan(hour, units):
    plan = {u: HOME[u] for u in units}
    n = len(units)
    k = 3 if n >= 26 else 2 if n >= 22 else 1 if n >= 18 else 0
    if k == 0:
        return plan
    by_post = {}
    for u in sorted(units):
        by_post.setdefault(HOME[u], []).append(u)
    for t in TARGETS[:k]:
        if by_post.get(t):
            continue
        donors = [p for p in by_post if len(by_post[p]) >= 4 and p not in TARGETS]
        if not donors:
            break
        d = max(donors, key=lambda p: (len(by_post[p]), -p))
        u = by_post[d].pop()
        plan[u] = t
        by_post.setdefault(t, []).append(u)
    return plan


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]
