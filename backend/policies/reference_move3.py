from collections import Counter

TARGETS = [41, 11, 40]
counts = Counter(HOME.values())
donors = []
for p, n in counts.most_common():
    donors += sorted(u for u in HOME if HOME[u] == p)[: max(n - 2, 0)]
MOVED = dict(zip(donors, TARGETS))


def post_plan(hour, units):
    return {u: MOVED.get(u, HOME[u]) for u in units}


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]
