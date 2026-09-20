#!/usr/bin/env python3
"""Figures from results/run.json and the derived tables -> results/figures/*.png.   python scripts/plots.py [results_dir]"""
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from futdesk import data  # noqa: E402

R = sys.argv[1] if len(sys.argv) > 1 else data.RESULTS
FIG = os.path.join(R, "figures"); os.makedirs(FIG, exist_ok=True)
run = json.load(open(os.path.join(R, "run.json"), encoding="utf-8"))
C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]      # the validated categorical slots, fixed order
GREY = "#8a8f99"
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.25, "figure.dpi": 130, "legend.frameon": False})


def save(fig, name):
    fig.tight_layout(); fig.savefig(os.path.join(FIG, name)); plt.close(fig); print("  ", name)


def der(name):
    p = os.path.join(data.DER, name)
    return pd.read_parquet(p) if os.path.exists(p) else pd.DataFrame()


# ---- queue value ------------------------------------------------------------------------------------------------------
for sym, q in run.get("queue", {}).items():
    if "tau_10s" not in q:
        continue
    g = q["tau_10s"]["groups"]; order = ["fifo_back_1", "fifo_mid_1", "fifo_front_1", "prorata_back_1", "prorata_back_10", "prorata_back_50", "prorata_front_10"]
    order = [k for k in order if k in g]
    fig, ax = plt.subplots(2, 2, figsize=(10, 7))
    a = ax[0, 0]; x = np.arange(len(order))
    vals = [g[k]["value_per_lot"] for k in order]; lo = [g[k]["value_per_lot"] - g[k]["ci"][0] for k in order]; hi = [g[k]["ci"][1] - g[k]["value_per_lot"] for k in order]
    cols = [C[0] if k.startswith("fifo") else C[1] for k in order]
    a.bar(x, vals, color=cols, width=0.6); a.errorbar(x, vals, yerr=[lo, hi], fmt="none", ecolor="black", capsize=3, lw=1)
    a.set_xticks(x); a.set_xticklabels([k.replace("_", "\n") for k in order], fontsize=7); a.axhline(0, color="black", lw=0.8)
    a.set_ylabel("value per lot posted (ticks, 10 s mark-out)"); a.set_title(f"{sym}: shadow orders by rule and queue position (95 % bands)")
    a = ax[0, 1]
    for k, c in zip(order, cols):
        a.scatter(g[k]["p_any_fill"], g[k]["markout_per_filled_lot"], color=c, s=40); a.annotate(k, (g[k]["p_any_fill"], g[k]["markout_per_filled_lot"]), fontsize=6, xytext=(3, 3), textcoords="offset points")
    a.set_xlabel("P(filled within 120 s)"); a.set_ylabel("mark-out per filled lot (ticks)"); a.set_title("fill probability against the mark-out of what fills")
    a = ax[1, 0]
    for k, c, lab in (("fifo_front_1", C[0], "FIFO front"), ("fifo_back_1", C[2], "FIFO back"), ("prorata_front_10", C[1], "pro-rata TOP 10"), ("prorata_back_10", C[3], "pro-rata back 10")):
        if k in g:
            ys = [q[f"tau_{t}s"]["groups"][k]["value_per_lot"] for t in (1, 10, 60)]; a.plot([1, 10, 60], ys, "-o", color=c, lw=2, ms=5, label=lab)
    a.set_xscale("log"); a.set_xlabel("mark-out horizon (s)"); a.set_ylabel("value per lot (ticks)"); a.legend(); a.set_title("by mark-out horizon")
    a = ax[1, 1]
    ro = q.get("real_orders", {}).get("by_ahead", [])
    if ro:
        d = pd.DataFrame(ro); a.bar(np.arange(len(d)), d["p_fill"], color=C[0], width=0.6); a.set_xticks(np.arange(len(d))); a.set_xticklabels(d["ahead_bucket"], fontsize=8)
        a.set_xlabel("lots ahead when the order joined the touch"); a.set_ylabel("share filled"); a.set_title(f"real orders at the touch (n = {q['real_orders']['n_orders']:,})")
    save(fig, f"queue_{sym}.png")
    # the book
    b = q["book"]; bh = pd.DataFrame(b["by_hour"])
    if len(bh):
        fig, ax = plt.subplots(1, 3, figsize=(11, 3.2))
        ax[0].bar(bh["hour"], bh["events"] / 1000, color=C[0], width=0.8); ax[0].set_title("events per hour (thousands)"); ax[0].set_xlabel("hour UTC")
        ax[1].plot(bh["hour"], bh["touch_depth"], "-o", color=C[1], ms=4, lw=2); ax[1].set_title("time-weighted depth at the touch (lots)"); ax[1].set_xlabel("hour UTC")
        ax[2].plot(bh["hour"], bh["spread_ticks"], "-o", color=C[2], ms=4, lw=2); ax[2].set_title("time-weighted spread (ticks)"); ax[2].set_xlabel("hour UTC"); ax[2].set_ylim(0.95, max(1.15, bh["spread_ticks"].max() + 0.02))
        save(fig, f"book_{sym}.png")

# ---- the market maker -------------------------------------------------------------------------------------------------
mm = der("mm_fills.parquet")
if len(mm) and run.get("mm"):
    sym = mm["symbol"].iloc[0]; o = run["mm"][sym]; tv = o["usd"]["spread_capture"] / o["ticks"]["spread_capture"] if o["ticks"]["spread_capture"] else 12.5
    mm = mm.sort_values("t"); t = pd.to_datetime(mm["t"], unit="ns", utc=True)
    fig, ax = plt.subplots(1, 3, figsize=(12, 3.4))
    cum_sc = np.where(mm["kind"] == 0, mm["edge_ticks"], 0).cumsum() * tv; cum_inv = mm["inventory_ticks"].cumsum() * tv; cum_h = np.where(mm["kind"] == 1, mm["edge_ticks"], 0).cumsum() * tv
    fees = -np.cumsum(mm["qty"]) * abs(o["usd"]["fees"]) / mm["qty"].sum()
    ax[0].plot(t, cum_sc, color=C[0], lw=2, label="spread capture"); ax[0].plot(t, cum_inv, color=C[1], lw=2, label="inventory"); ax[0].plot(t, cum_h, color=C[2], lw=2, label="hedge")
    ax[0].plot(t, fees, color=C[3], lw=2, label="fees"); ax[0].plot(t, cum_sc + cum_inv + cum_h + fees, color="black", lw=1.5, label="total"); ax[0].legend(fontsize=7); ax[0].set_title(f"{sym} one-lot quoter: cumulative P&L (USD)")
    ax[0].tick_params(axis="x", labelrotation=30)
    k = ["spread_capture", "inventory", "hedge", "fees", "total_after_fees"]; v = [o["per_passive_lot_usd"][x] for x in k]
    ax[1].bar(range(5), v, color=[C[0], C[1], C[2], C[3], "black"], width=0.6); ax[1].set_xticks(range(5)); ax[1].set_xticklabels(["spread", "inventory", "hedge", "fees", "total"]); ax[1].axhline(0, color="black", lw=0.8)
    ax[1].set_title("per passive fill (USD)")
    ax[2].plot(t, mm["inv_after"], color=C[0], lw=0.8); ax[2].set_title("inventory (lots)"); ax[2].tick_params(axis="x", labelrotation=30)
    save(fig, "mm.png")

# ---- rolls -----------------------------------------------------------------------------------------------------------
win = der("roll_windows.parquet"); ev = der("roll_events.parquet"); eia_ev = der("roll_eia_events.parquet"); eia_p = der("roll_eia_paths.parquet"); sp = der("roll_spreads.parquet")
fig, ax = plt.subplots(2, 2, figsize=(11, 7))
a = ax[0, 0]
if len(win):
    for i, root in enumerate([r for r in ("ES", "NQ", "ZN", "CL") if r in set(win["root"])]):
        w = win[win["root"] == root].groupby("bd_to_roll")["far_vol_share"].mean()
        a.plot(-w.index, w.values, "-o", color=C[i], ms=4, lw=2, label=root)
    a.set_xlabel("business days to the roll date (negative = before)"); a.set_ylabel("far contract's share of volume"); a.legend(); a.set_title("volume migrates to the far contract")
a = ax[0, 1]
if len(sp) and "richness_bp" in sp:
    for i, root in enumerate(("ES", "NQ")):
        s = sp[sp["root"] == root].sort_values("date"); s = s.groupby("date").first(); s.index = pd.to_datetime(s.index)
        s = s[s.index >= s.index.max() - pd.Timedelta(days=365)]
        a.plot(s.index, s["richness_bp"].rolling(5).mean(), color=C[i], lw=1.5, label=root)
    a.axhline(0, color="black", lw=0.8); a.set_ylabel("implied financing minus SOFR (bp, 5-day mean)"); a.legend(); a.set_title("the nearest calendar spread against fair value"); a.tick_params(axis="x", labelrotation=30)
a = ax[1, 0]
if len(eia_ev):
    for i, root in enumerate(("CL", "NG")):
        e = eia_ev[eia_ev["root"] == root]
        if len(e):
            e = e.assign(year=pd.to_datetime(e["roll_date"]).dt.year).groupby("year")["roll_cost_long_usd"].mean()
            a.plot(e.index, e.values, "-", color=C[i], lw=2, label=root)
    a.axhline(0, color="black", lw=0.8); a.set_ylabel("roll cost for a long, USD per contract (yearly mean)"); a.legend(); a.set_title("forty years of monthly energy rolls (EIA settlements)")
a = ax[1, 1]
if len(eia_p):
    for i, root in enumerate(("CL", "NG")):
        p = eia_p[eia_p["root"] == root] if "root" in eia_p else eia_p
        if "root" not in eia_p and i > 0:
            break
        g = p.groupby("bd_to_roll")["spread_usd"]; m = g.median(); q1 = g.quantile(0.25); q3 = g.quantile(0.75)
        a.plot(-m.index, m.values, color=C[i], lw=2, label=f"{root} median"); a.fill_between(-m.index, q1.values, q3.values, color=C[i], alpha=0.15)
    a.set_xlabel("business days to the roll date"); a.set_ylabel("c2 - c1, USD per contract"); a.legend(); a.set_title("the spread through the roll window (interquartile band)")
save(fig, "roll.png")

# ---- the stated book --------------------------------------------------------------------------------------------------
bd = der("book_daily.parquet")
if len(bd):
    d = bd.groupby("date")[["variation", "price", "roll", "execution"]].sum(); fin = bd.groupby("date")[["financing", "initial_margin"]].first()
    d = d.join(fin); d.index = pd.to_datetime(d.index)
    fig, ax = plt.subplots(1, 3, figsize=(12, 3.5))
    for k, c in (("price", C[0]), ("roll", C[1]), ("execution", C[2]), ("financing", C[3])):
        ax[0].plot(d.index, d[k].cumsum() / 1e3, color=c, lw=2, label=k)
    ax[0].plot(d.index, (d["variation"] + d["execution"] + d["financing"]).cumsum() / 1e3, color="black", lw=1.5, label="total"); ax[0].legend(fontsize=7); ax[0].set_title("the stated book: cumulative components (USD thousands)"); ax[0].tick_params(axis="x", labelrotation=30)
    br = bd.groupby("root")[["price", "roll", "execution"]].sum() / 1e3
    x = np.arange(len(br)); ax[1].bar(x - 0.2, br["price"], 0.4, color=C[0], label="price"); ax[1].bar(x + 0.2, br["roll"], 0.4, color=C[1], label="roll"); ax[1].set_xticks(x); ax[1].set_xticklabels(br.index); ax[1].legend(); ax[1].axhline(0, color="black", lw=0.8); ax[1].set_title("by product (USD thousands)")
    ax[2].bar(d.index, d["variation"] / 1e3, color=[C[0] if v >= 0 else C[1] for v in d["variation"]], width=0.8); ax[2].axhline(0, color="black", lw=0.8)
    ax[2].set_title(f"daily settlement variation (USD thousands; margin {d['initial_margin'].mean() / 1e6:.2f}m)"); ax[2].tick_params(axis="x", labelrotation=30)
    save(fig, "book.png")

# ---- the monitor ------------------------------------------------------------------------------------------------------
mo = run.get("monitor", {}); bk = mo.get("by_kind", {})
if bk:
    fig, ax = plt.subplots(1, 3, figsize=(12, 3.4))
    kinds = list(bk); inj = [bk[k]["injected"] for k in kinds]; ca = [bk[k]["caught"] for k in kinds]
    x = np.arange(len(kinds)); ax[0].bar(x, inj, color=GREY, width=0.6, label="injected"); ax[0].bar(x, ca, color=C[0], width=0.6, label="caught"); ax[0].set_xticks(x); ax[0].set_xticklabels(kinds, rotation=60, ha="right", fontsize=7); ax[0].legend(); ax[0].set_title("faults by kind")
    ttd = [bk[k]["median_ttd_s"] or 0 for k in kinds]; ax[1].bar(x, np.maximum(ttd, 0.5), color=C[1], width=0.6); ax[1].set_yscale("log"); ax[1].set_xticks(x); ax[1].set_xticklabels(kinds, rotation=60, ha="right", fontsize=7); ax[1].set_title("median time to detect (s, log)")
    al = der("monitor_alerts.parquet")
    if len(al):
        per = al.groupby(["date", "fault_day"]).size().reset_index(name="n")
        ax[2].bar(np.arange(len(per)), per["n"], color=[C[1] if f else C[2] for f in per["fault_day"]], width=0.7); ax[2].set_title("alerts per day (orange = fault days, aqua = clean)"); ax[2].set_xlabel("day")
    save(fig, "monitor.png")

# ---- matching ---------------------------------------------------------------------------------------------------------
m = run.get("matching", {})
if m:
    fig, ax = plt.subplots(figsize=(7, 3.2))
    rules = list(m["rules"]); x = np.arange(len(rules)); w = 0.2
    for i, oid in enumerate(["1", "2", "3", "4"]):
        ax.bar(x + (i - 1.5) * w, [m["rules"][r].get(oid, 0) for r in rules], w, color=C[i], label=f"order {oid} ({m['resting'][i]} lots)")
    ax.set_xticks(x); ax.set_xticklabels([r.replace(", ", "\n").replace(": ", "\n") for r in rules], fontsize=7); ax.set_ylabel("lots allocated of 60"); ax.legend(fontsize=7); ax.set_title("the allocation rules on one level")
    save(fig, "matching.png")
print("figures ->", FIG)
