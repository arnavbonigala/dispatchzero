import json
from pathlib import Path

HOME = json.loads((Path(__file__).parents[2] / "data" / "units.json").read_text())


def post_plan(hour, units):
    return {u: HOME[u] for u in units}


def choose_unit(call, idle, eta):
    return min(idle, key=lambda u: eta(u, call))["unit_id"]
