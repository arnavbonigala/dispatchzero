import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

DATA = Path(__file__).parents[1] / "data"
HEAT_C = 25.0
RAIN_MM = 0.1
RIDGE = 1e-3


def design(grid, nbhds):
    hours = np.eye(24)[grid["hour"].to_numpy()][:, 1:]
    fe = (grid["nbhd"].to_numpy()[:, None] == np.array(nbhds)[None, :]).astype(float)
    x = np.column_stack([fe, hours, grid["weekend"], grid["heat"], grid["rain"]])
    return x


def fit_poisson(x, y):
    def nll(b):
        eta = np.clip(x @ b, -30, 30)
        mu = np.exp(eta)
        return (mu - y * eta).sum() + RIDGE * (b @ b), x.T @ (mu - y) + 2 * RIDGE * b

    b0 = np.zeros(x.shape[1])
    return minimize(nll, b0, jac=True, method="L-BFGS-B").x


def deviance(y, mu):
    mu = np.maximum(mu, 1e-9)
    term = np.where(y > 0, y * np.log(np.where(y > 0, y, 1) / mu), 0.0)
    return float(2 * (term - (y - mu)).sum())


def build_grid(calls, weather, nbhds):
    hours = pd.Series(sorted(weather))
    grid = pd.DataFrame([(n, h) for h in hours for n in nbhds], columns=["nbhd", "ts"])
    ts = pd.to_datetime(grid["ts"], format="%Y-%m-%dT%H")
    grid["hour"] = ts.dt.hour
    grid["weekend"] = (ts.dt.weekday >= 5).astype(float)
    grid["heat"] = [max(weather[h]["temp_c"] - HEAT_C, 0.0) for h in grid["ts"]]
    grid["rain"] = [float(weather[h]["rain_mm"] > RAIN_MM) for h in grid["ts"]]
    counts = calls.assign(ts=calls["t"].str[:13]).groupby(["nbhd", "ts"]).size()
    grid["y"] = [counts.get((n, h), 0) for n, h in zip(grid["nbhd"], grid["ts"])]
    split_of = calls.assign(ts=calls["t"].str[:13]).groupby("ts")["split"].first()
    day_split = calls.assign(d=calls["t"].str[:10]).groupby("d")["split"].first()
    grid["split"] = [split_of.get(h, day_split.get(h[:10])) for h in grid["ts"]]
    return grid


def run(calls, weather, nbhd_info):
    nbhds = sorted(set(calls["nbhd"]) & set(nbhd_info))
    grid = build_grid(calls[calls["nbhd"].isin(nbhds)], weather, nbhds)
    x = design(grid, nbhds)
    train = (grid["split"] == "train").to_numpy()
    hold = (grid["split"] == "holdout").to_numpy()
    b = fit_poisson(x[train], grid["y"].to_numpy()[train].astype(float))
    grid["mu"] = np.exp(x @ b)

    tr = grid[train]
    naive = tr.groupby(["nbhd", "hour"])["y"].mean()
    grid["naive"] = [naive.get((n, h), 0.0) for n, h in zip(grid["nbhd"], grid["hour"])]
    h = grid[hold]
    y = h["y"].to_numpy().astype(float)
    model_dev, naive_dev = deviance(y, h["mu"].to_numpy()), deviance(y, h["naive"].to_numpy())

    k = len(nbhds)
    fe = b[:k]
    weights = tr.groupby("nbhd")["y"].sum().reindex(nbhds).to_numpy() + 1
    z = np.column_stack([np.ones(k), np.log([nbhd_info[n]["population"] for n in nbhds]), [nbhd_info[n]["pct_65_plus"] for n in nbhds]])
    w = np.sqrt(weights)[:, None]
    g = np.linalg.lstsq(z * w, fe * w[:, 0], rcond=None)[0]

    report = {
        "fit_on": "train",
        "holdout_hours": int(hold.sum() // k),
        "holdout_deviance": {"model": round(model_dev, 1), "naive": round(naive_dev, 1)},
        "holdout_mae": {"model": round(float(np.abs(h["mu"] - y).mean()), 4), "naive": round(float(np.abs(h["naive"] - y).mean()), 4)},
        "beats_naive": model_dev < naive_dev,
        "naive": "train average per neighborhood and hour of day",
        "effects": {
            "weekend_x": round(float(np.exp(b[k + 23])), 3),
            "heat_per_c_above_25_x": round(float(np.exp(b[k + 24])), 3),
            "rain_x": round(float(np.exp(b[k + 25])), 3),
            "pct_65_plus_per_10pts_x": round(float(np.exp(10 * g[2])), 3),
            "population_elasticity": round(float(g[1]), 3),
        },
        "rain_hours_in_train": int(tr.drop_duplicates("ts")["rain"].sum()),
        "heat_hours_in_train": int((tr.drop_duplicates("ts")["heat"] > 0).sum()),
    }
    out = {f"{n}|{t}": round(float(m), 4) for n, t, m in zip(grid["nbhd"], grid["ts"], grid["mu"])}
    out["_report"] = report
    return out


def self_check():
    rng = np.random.default_rng(0)
    nbhds = [f"N{i}" for i in range(6)]
    info = {n: {"population": 10000 * (i + 1), "pct_65_plus": 10 + 3 * i} for i, n in enumerate(nbhds)}
    hours = pd.date_range("2026-01-05", periods=24 * 28, freq="h")
    weather = {h.strftime("%Y-%m-%dT%H"): {"rain_mm": float(rng.random() < 0.15) * 2.0, "temp_c": 18 + 12 * rng.random()} for h in hours}
    rows = []
    for h in hours:
        key = h.strftime("%Y-%m-%dT%H")
        wx = weather[key]
        for i, n in enumerate(nbhds):
            lam = 0.3 * (i + 1) * (1.5 if 9 <= h.hour <= 20 else 0.6) * (1.3 if wx["rain_mm"] > RAIN_MM else 1.0) * (1.1 ** max(wx["temp_c"] - HEAT_C, 0))
            split = "train" if h < hours[24 * 21] else "holdout"
            rows += [{"t": key + ":00:00", "nbhd": n, "split": split}] * rng.poisson(lam)
    out = run(pd.DataFrame(rows), weather, info)
    r = out["_report"]
    assert r["beats_naive"] or r["holdout_deviance"]["model"] <= r["holdout_deviance"]["naive"] * 1.02, r
    assert 1.15 < r["effects"]["rain_x"] < 1.45, r["effects"]
    assert 0.9 < r["effects"]["weekend_x"] < 1.1, r["effects"]
    assert len(out) == 6 * len(hours) + 1
    assert all(v >= 0 for k, v in out.items() if k != "_report")
    print("forecast self-check ok", json.dumps(r["effects"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    calls = pd.read_parquet(DATA / "calls.parquet", columns=["t", "nbhd", "split"])
    out = run(calls, json.loads((DATA / "weather.json").read_text()), json.loads((DATA / "neighborhoods.json").read_text()))
    (DATA / "forecast.json").write_text(json.dumps(out))
    print(json.dumps(out["_report"], indent=1))


if __name__ == "__main__":
    main()
