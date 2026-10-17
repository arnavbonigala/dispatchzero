SLOW = {
    "Visitacion Valley",
    "Haight Ashbury",
    "Excelsior",
    "Oceanview/Merced/Ingleside",
    "Sunset/Parkside",
    "Lakeshore",
    "Portola",
    "Inner Sunset",
    "Outer Richmond",
    "Inner Richmond",
}


def post_plan(hour, units):
    plan = {u: HOME[u] for u in units}
    try:
        b = BUCKET_OF.get(hour)
        if b is None:
            b = BUCKET_OF.get(str(hour))
        dem = DEMAND.get(b, {})
        n = {}
        for u in units:
            p = plan[u]
            n[p] = n.get(p, 0) + 1
        for _ in range(3):
            rec = None
            rec_score = 0.0
            for p, d in dem.items():
                if d <= 0:
                    continue
                k = n.get(p, 0)
                if k > 1:
                    continue
                score = d / (k + 1)
                if score > rec_score:
                    rec = p
                    rec_score = score
            if rec is None:
                break
            don = None
            don_load = None
            for q, k in n.items():
                if k < 2 or q == rec:
                    continue
                dr = POST_DRIVE.get(q, {}).get(rec)
                if dr is None or dr > 240:
                    continue
                load = dem.get(q, 0) / (k - 1)
                if load < 0.5 * rec_score and (don_load is None or load < don_load):
                    don = q
                    don_load = load
            if don is None:
                break
            for u in units:
                if plan[u] == don and HOME[u] == don:
                    plan[u] = rec
                    n[don] -= 1
                    n[rec] = n.get(rec, 0) + 1
                    break
            else:
                break
    except Exception:
        return {u: HOME[u] for u in units}
    return plan


def choose_unit(call, idle, eta):
    etas = {}
    for u in idle:
        etas[u["unit_id"]] = eta(u, call)
    closest = min(idle, key=lambda u: etas[u["unit_id"]])
    try:
        lt = call.life_threatening
    except Exception:
        lt = call["life_threatening"]
    if lt or len(idle) < 2:
        return closest["unit_id"]
    try:
        counts = {}
        for u in idle:
            p = u.get("post")
            counts[p] = counts.get(p, 0) + 1
        limit = etas[closest["unit_id"]] + 60
        best = closest
        best_key = None
        for u in idle:
            s = etas[u["unit_id"]]
            if s > limit:
                continue
            key = (-counts.get(u.get("post"), 0), s)
            if best_key is None or key < best_key:
                best = u
                best_key = key
        return best["unit_id"]
    except Exception:
        return closest["unit_id"]


def send_engine(call, engines, eta, ambulance_s):
    try:
        h = call.hour
    except Exception:
        h = call["hour"]
    try:
        n = call.nbhd
    except Exception:
        n = call["nbhd"]
    slow = n in SLOW
    if slow:
        min_amb = 350
        margin = 60
        cap = 0.7
    else:
        min_amb = 440
        margin = 85
        cap = 0.45
        if 10 <= h <= 15:
            min_amb = 420
    if ambulance_s < min_amb:
        return None
    best = None
    best_s = None
    for e in engines:
        uid = e["unit_id"]
        load = ENGINE_LOAD.get(uid + "|" + str(h), 0.0)
        s = eta(e, call)
        gain = ambulance_s - s
        if gain < margin:
            continue
        lim = cap
        if not slow and 10 <= h <= 15 and gain >= 300:
            lim = 0.8
        if load > lim:
            continue
        if best_s is None or s < best_s:
            best = uid
            best_s = s
    return best
