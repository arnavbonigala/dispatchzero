import json
import statistics as st
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PatchCollection
from matplotlib.patches import Polygon

ROOT = Path(__file__).parents[1]
OUT = ROOT / "docs" / "charts"
BASE, GM, REAL, INK, MUTED, GRID = "#a3a9b1", "#14275a", "#e5383b", "#1f2328", "#57606a", "#e6e8eb"
MIN_CALLS = 20

plt.rcParams.update({
    "font.family": ["Helvetica Neue", "Arial", "DejaVu Sans"], "font.size": 11, "axes.edgecolor": GRID,
    "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False,
    "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.axisbelow": True, "figure.dpi": 150, "savefig.bbox": "tight", "savefig.facecolor": "white",
})


def results(name):
    return json.loads((ROOT / "runs" / "results" / f"{name}_frozen_engines.json").read_text())["results"]


def mmss(s, _=None):
    s = round(s)
    return f"{s // 60}:{s % 60:02d}"


def title(ax, text, sub):
    ax.set_title(f"{text}\n", loc="left", fontsize=14, fontweight="bold", color=INK)
    ax.text(0, 1.02, sub, transform=ax.transAxes, fontsize=10, color=MUTED)


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / name)
    plt.close(fig)


def by(rows, key):
    g = defaultdict(list)
    for r in rows:
        g[r[key]].append(r)
    return g


def headline():
    h = json.loads((ROOT / "fixtures" / "engines_headline.json").read_text())
    keys = ["mean_s", "slow_median_s", "p90_s"]
    b, g = [h["baseline"][k] for k in keys], [h["frozen"][k] for k in keys]
    labels = ["Average, whole city", "Median, 10 slowest\nneighborhoods", "Slowest 10% of calls\n(90th percentile)"]
    fig, ax = plt.subplots(figsize=(9, 4.4))
    y = range(len(labels))
    ax.barh([i + 0.2 for i in y], b, height=0.38, color=BASE, label="Standard dispatch")
    ax.barh([i - 0.2 for i in y], g, height=0.38, color=GM, label="DispatchZero")
    for i in y:
        ax.text(b[i] + 8, i + 0.2, mmss(b[i]), va="center", color=MUTED)
        ax.text(g[i] + 8, i - 0.2, f"{mmss(g[i])}   {mmss(b[i] - g[i])} faster", va="center", color=INK, fontweight="bold")
    ax.set_yticks(list(y), labels)
    ax.invert_yaxis()
    ax.xaxis.set_major_locator(matplotlib.ticker.MultipleLocator(180))
    ax.xaxis.set_major_formatter(mmss)
    ax.set_xlim(0, max(b) * 1.3)
    ax.grid(axis="y", visible=False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=2, frameon=False)
    title(ax, "Time to the first paramedic on scene", f"{h['frozen']['n']:,} real SF 911 calls from a week the AI never saw")
    save(fig, "headline.png")


def neighborhoods(base, gm, slow):
    b, g = by(base, "nbhd"), by(gm, "nbhd")
    names = sorted(slow, key=lambda n: st.median(r["response_s"] for r in b[n]))
    fig, ax = plt.subplots(figsize=(9, 5.2))
    for i, n in enumerate(names):
        x0, x1 = st.median(r["response_s"] for r in b[n]), st.median(r["response_s"] for r in g[n])
        ax.plot([x0, x1], [i, i], color=GRID, lw=3, zorder=1)
        ax.scatter([x0], [i], color=BASE, s=45, zorder=2)
        ax.scatter([x1], [i], color=GM, s=60, zorder=3)
        if x0 - x1 >= 30:
            ax.text(x0 + 10, i, f"{mmss(x0 - x1)} faster", va="center", color=INK, fontsize=9)
    ax.set_yticks(range(len(names)), names)
    ax.xaxis.set_major_locator(matplotlib.ticker.MultipleLocator(60))
    ax.xaxis.set_major_formatter(mmss)
    ax.set_xlim(right=ax.get_xlim()[1] + 70)
    ax.grid(axis="y", visible=False)
    ax.scatter([], [], color=BASE, label="Standard dispatch")
    ax.scatter([], [], color=GM, label="DispatchZero")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=2, frameon=False)
    title(ax, "The longest waits shrink the most",
          "Median time to first paramedic in the 10 neighborhoods that wait longest under standard dispatch")
    save(fig, "equity_neighborhoods.png")


def rings(geom):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    return [p[0] for p in polys]


def improvement_map(base, gm):
    geo = json.loads((ROOT / "fixtures" / "neighborhoods.geojson").read_text())
    meds = []
    for rows in (base, gm):
        g = by(rows, "nbhd")
        meds.append({n: st.median(r["response_s"] for r in g[n]) for n in g if len(g[n]) >= MIN_CALLS})
    lo, hi = min(min(m.values()) for m in meds), max(max(m.values()) for m in meds)
    cmap, norm = plt.get_cmap("RdYlBu_r"), plt.Normalize(lo, hi)
    fig, axes = plt.subplots(1, 2, figsize=(11, 5.6))
    for ax, med, name in zip(axes, meds, ["Standard dispatch", "DispatchZero"]):
        patches, colors = [], []
        for f in geo["features"]:
            n = f["properties"]["nhood"]
            for ring in rings(f["geometry"]):
                patches.append(Polygon(ring, closed=True))
                colors.append(cmap(norm(med[n])) if n in med else "#eef0f2")
        ax.add_collection(PatchCollection(patches, facecolor=colors, edgecolor="white", linewidth=0.7))
        ax.set_aspect(1 / 0.79)
        ax.set_xlim(-122.52, -122.355)
        ax.set_ylim(37.705, 37.815)
        ax.axis("off")
        spread = st.stdev(med.values())
        ax.set_title(f"{name}\nneighborhood spread {mmss(spread)}", fontsize=12, color=INK, fontweight="bold")
    cb = fig.colorbar(plt.cm.ScalarMappable(cmap=cmap, norm=norm), ax=axes, shrink=0.7, pad=0.02, format=mmss)
    cb.set_label("Median time to first paramedic", color=MUTED)
    cb.outline.set_visible(False)
    fig.suptitle("The map evens out", x=0.06, ha="left", fontsize=15, fontweight="bold", color=INK)
    fig.text(0.06, 0.9, f"Median time to first paramedic by neighborhood, unseen week. Grey: fewer than {MIN_CALLS} calls",
             fontsize=10, color=MUTED)
    save(fig, "equity_map.png")


def distribution(base, gm):
    fig, ax = plt.subplots(figsize=(9, 4.4))
    for rows, color, label in [(base, BASE, "Standard dispatch"), (gm, GM, "DispatchZero")]:
        xs = sorted(r["response_s"] for r in rows)
        share = sum(x <= 480 for x in xs) / len(xs)
        ax.plot(xs, [100 * (i + 1) / len(xs) for i in range(len(xs))], color=color, lw=2.5,
                label=f"{label}: {share:.0%} within 8 min")
    ax.axvline(480, color=INK, lw=1, ls="--")
    ax.text(486, 6, "8 minutes", color=INK)
    ax.xaxis.set_major_formatter(mmss)
    ax.set_xlim(120, 1500)
    ax.set_ylabel("Share of calls reached (%)")
    ax.legend(loc="lower right", frameon=False)
    title(ax, "More patients reached within 8 minutes", "Cumulative share of calls with a paramedic on scene, unseen week")
    save(fig, "distribution.png")


def by_hour(base, gm):
    b, g = by(base, "hour"), by(gm, "hour")
    hours = range(24)
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(hours, [st.mean(r["response_s"] for r in b[h]) for h in hours], color=BASE, lw=2.5, label="Standard dispatch")
    ax.plot(hours, [st.mean(r["response_s"] for r in g[h]) for h in hours], color=GM, lw=2.5, label="DispatchZero")
    ax.set_xticks(range(0, 24, 3), [f"{h:02d}:00" for h in range(0, 24, 3)])
    ax.yaxis.set_major_formatter(mmss)
    ax.legend(loc="upper right", frameon=False)
    title(ax, "Faster at every hour of the day", "Average time to first paramedic by hour the call came in, unseen week")
    save(fig, "by_hour.png")


def calibration(base):
    b = by(base, "hour")
    hours = range(24)
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(hours, [st.median(r["historical_first_s"] for r in b[h]) for h in hours], color=REAL, lw=2.5,
            label="Real SF history")
    ax.plot(hours, [st.median(r["response_s"] for r in b[h]) for h in hours], color=BASE, lw=2.5, ls="--",
            label="Digital twin, standard dispatch")
    ax.set_xticks(range(0, 24, 3), [f"{h:02d}:00" for h in range(0, 24, 3)])
    ax.yaxis.set_major_formatter(mmss)
    ax.legend(loc="upper right", frameon=False)
    real = st.median(r["historical_first_s"] for r in base)
    sim = st.median(r["response_s"] for r in base)
    title(ax, "The digital twin reproduces San Francisco",
          f"Median time to first paramedic by hour, unseen week. Overall: real {mmss(real)}, twin {mmss(sim)}")
    save(fig, "calibration.png")


def eta_model():
    rep = json.loads((ROOT / "data" / "eta.json").read_text())["report"]
    err, med = rep["holdout_median_abs_err_s"], rep["holdout_median_s"]
    fig, (a, b) = plt.subplots(1, 2, figsize=(9, 3.8))
    a.bar(["Road-time formula", "Learned model"], [err["current"], err["learned"]], color=[BASE, GM], width=0.55)
    for i, v in enumerate([err["current"], err["learned"]]):
        a.text(i, v + 4, f"{v} s", ha="center", color=INK, fontweight="bold")
    a.set_title("Typical error per drive", loc="left", color=INK)
    a.grid(axis="x", visible=False)
    b.bar(["Real drives", "Formula", "Learned"], [med["actual"], med["current"], med["learned"]], color=[REAL, BASE, GM], width=0.55)
    for i, v in enumerate([med["actual"], med["current"], med["learned"]]):
        b.text(i, v + 8, mmss(v), ha="center", color=INK, fontweight="bold")
    b.yaxis.set_major_formatter(mmss)
    b.set_title("Median drive time", loc="left", color=INK)
    b.grid(axis="x", visible=False)
    fig.suptitle("Learned drive times, tested on a held-out week", x=0.06, ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.tight_layout()
    save(fig, "eta_model.png")


def learning_curve():
    rows = [json.loads(x) for x in (ROOT / "fixtures" / "engines_versions.jsonl").read_text().splitlines() if x.strip()]
    rows = [r for r in rows if r.get("train_mean")]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    xs, ys = [], []
    for i, r in enumerate(rows):
        if r["accepted"]:
            xs.append(i)
            ys.append(r["train_mean"])
            ax.scatter([i], [r["train_mean"]], color=GM, s=55, zorder=3)
        else:
            ax.scatter([i], [r["train_mean"]], color=BASE, s=24, marker="x", zorder=2)
    xs.append(len(rows) - 1)
    ys.append(ys[-1])
    ax.step(xs, ys, where="post", color=GM, lw=2.5, zorder=2)
    ax.annotate(f"Standard dispatch {mmss(ys[0])}", (0, ys[0]), xytext=(8, 8), textcoords="offset points", color=MUTED)
    ax.annotate(f"Best rule {mmss(ys[-1])}", (xs[-1], ys[-1]), xytext=(-8, -16), textcoords="offset points", ha="right",
                color=INK, fontweight="bold")
    ax.yaxis.set_major_formatter(mmss)
    ax.set_ylim(min(r["train_mean"] for r in rows) - 15, max(r["train_mean"] for r in rows) + 10)
    ax.set_xlabel("Rules written by the agent, in order")
    ax.scatter([], [], color=GM, s=55, label="Kept: passed every gate")
    ax.scatter([], [], color=BASE, s=24, marker="x", label="Rejected")
    ax.legend(loc="upper right", frameon=False)
    title(ax, "The agent teaches itself", "Average time to first paramedic on the training weeks, for every rule it wrote")
    save(fig, "learning_curve.png")


def main():
    best = json.loads((ROOT / "fixtures" / "engines_headline.json").read_text())["version"]
    base, gm = results("baseline"), results(best)
    slow = set(json.loads((ROOT / "data" / "slow_nbhds.json").read_text())["nbhds"])
    headline()
    neighborhoods(base, gm, slow)
    improvement_map(base, gm)
    distribution(base, gm)
    by_hour(base, gm)
    calibration(base)
    eta_model()
    learning_curve()
    print("wrote", sorted(p.name for p in OUT.glob("*.png")))


if __name__ == "__main__":
    main()
