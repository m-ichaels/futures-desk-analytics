"""Roll dynamics and cost from daily settlements.

Calendar spreads between consecutive listed contracts; for the equity-index products the spread's implied financing
rate against SOFR net of the dividend yield (what the roll costs against fair value); the spread's path through the
roll window (business days to the roll date); the volume migration from the near to the far contract; and, from the
EIA history of NYMEX contracts 1-4, forty years of monthly energy rolls.  Roll cost per contract for a long = the
calendar spread paid (far minus near, positive in contango) plus crossing half a spread tick and two fees."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from . import calendars, products


def _ltd(row_root: str, year: int, month: int) -> dt.date:
    return calendars.last_trading_day(products.spec(row_root)["ltd_rule"], year, month)


def calendar_spreads(settle: pd.DataFrame, root: str) -> pd.DataFrame:
    """Every date x consecutive-contract pair: near/far closes, spread (far - near), days between expiries, days to the near roll."""
    s = settle[settle["root"] == root].copy()
    if s.empty:
        return pd.DataFrame()
    spec = products.spec(root)
    codes = s[["code", "year", "month"]].drop_duplicates().sort_values(["year", "month"])
    codes["ltd"] = [_ltd(root, y, m) for y, m in zip(codes["year"], codes["month"])]
    codes["roll"] = [calendars.contract_dates(spec, y, m)["roll"] for y, m in zip(codes["year"], codes["month"])]
    order = codes["code"].tolist()
    wide = s.pivot(index="date", columns="code", values="close").reindex(columns=order)
    vol = s.pivot(index="date", columns="code", values="volume").reindex(columns=order)
    rows = []
    ltd = dict(zip(codes["code"], codes["ltd"])); rolld = dict(zip(codes["code"], codes["roll"]))
    for i in range(len(order) - 1):
        near, far = order[i], order[i + 1]
        both = wide[[near, far]].dropna()
        if both.empty:
            continue
        d = pd.DataFrame({"date": both.index, "near": near, "far": far, "near_close": both[near].values, "far_close": both[far].values})
        d["spread"] = d["far_close"] - d["near_close"]
        d["days_between"] = (ltd[far] - ltd[near]).days
        d["days_to_ltd"] = [(ltd[near] - x).days for x in d["date"]]
        d["bd_to_roll"] = [np.busday_count(x, rolld[near]) if x <= rolld[near] else -np.busday_count(rolld[near], x) for x in d["date"]]
        d["near_vol"] = vol[near].reindex(both.index).values; d["far_vol"] = vol[far].reindex(both.index).values
        d["near_ltd"] = ltd[near]; d["near_roll"] = rolld[near]
        rows.append(d)
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if len(out):
        out["root"] = root
    return out


def implied_financing(spreads: pd.DataFrame, rates: pd.DataFrame, dividends: pd.DataFrame, index_prices: pd.DataFrame, root: str) -> pd.DataFrame:
    """Equity index calendar spread: F_far/F_near - 1 = (r - q) * dt, so r_implied = q + (F_far/F_near - 1) * 365/days.
    q is the ETF's trailing twelve-month dividend yield on the date; SOFR is the overnight rate on the date (a proxy for
    the term rate over the spread's tenor).  richness_bp = r_implied - SOFR; per contract in dollars over the tenor."""
    if spreads.empty:
        return spreads
    spec = products.spec(root); etf = spec.get("underlying_index")
    d = spreads.copy()
    r = rates.set_index("date")["SOFR"].sort_index()
    d["sofr"] = pd.Series(d["date"]).map(lambda x: r[:x].iloc[-1] if len(r[:x]) else np.nan).values / 100.0
    q = np.full(len(d), np.nan)
    if etf is not None and len(dividends) and len(index_prices):
        dv = dividends[dividends["symbol"] == etf].sort_values("date"); px = index_prices[index_prices["symbol"] == etf].set_index("date")["close"].sort_index()
        for k, x in enumerate(d["date"]):
            win = dv[(dv["date"] > x - dt.timedelta(days=365)) & (dv["date"] <= x)]
            p = px[:x]
            if len(win) and len(p):
                q[k] = win["dividend"].sum() / p.iloc[-1]
    d["div_yield"] = q
    d["implied_r"] = d["div_yield"] + (d["far_close"] / d["near_close"] - 1.0) * 365.0 / d["days_between"]
    d["richness_bp"] = (d["implied_r"] - d["sofr"]) * 1e4
    d["fair_spread"] = d["near_close"] * (d["sofr"] - d["div_yield"]) * d["days_between"] / 365.0
    d["richness_usd"] = (d["spread"] - d["fair_spread"]) * spec["multiplier"]
    return d


def roll_windows(spreads: pd.DataFrame, root: str, before: int = 15, after: int = 2) -> pd.DataFrame:
    """The near/far pair around each roll date in the data: spread path, volume share of the far leg, by business days to the roll."""
    if spreads.empty:
        return pd.DataFrame()
    w = spreads[(spreads["bd_to_roll"] <= before) & (spreads["bd_to_roll"] >= -after)].copy()
    w["far_vol_share"] = w["far_vol"] / (w["near_vol"] + w["far_vol"])
    w["root"] = root
    return w


def roll_events(spreads: pd.DataFrame, root: str) -> pd.DataFrame:
    """One row per roll of the near contract observed in the data: the spread on the roll date, its drift over the
    prior ten business days, the volume-crossover date, the execution cost, the total roll cost per contract."""
    if spreads.empty:
        return pd.DataFrame()
    spec = products.spec(root)
    out = []
    for near, g in spreads.groupby("near"):
        g = g.sort_values("date")
        on = g[g["bd_to_roll"] == 0]
        if on.empty or g["bd_to_roll"].min() > 0:
            continue
        on = on.iloc[-1]
        prior = g[(g["bd_to_roll"] >= 1) & (g["bd_to_roll"] <= 10)]
        cross = g[(g["far_vol"] > g["near_vol"]) & (g["bd_to_roll"] <= 15)]
        exec_cost = 0.5 * spec["spread_tick"] * spec["multiplier"] + 2 * spec["fee_per_side"]
        out.append({"root": root, "near": near, "far": on["far"], "roll_date": on["date"], "spread": on["spread"], "spread_usd": on["spread"] * spec["multiplier"],
                    "spread_10bd_before": prior["spread"].iloc[0] if len(prior) else np.nan, "drift_usd": (on["spread"] - prior["spread"].iloc[0]) * spec["multiplier"] if len(prior) else np.nan,
                    "spread_sd_usd_10bd": prior["spread"].std() * spec["multiplier"] if len(prior) > 2 else np.nan,
                    "volume_crossover": cross["date"].iloc[0] if len(cross) else None, "bd_crossover_to_roll": int(cross["bd_to_roll"].iloc[0]) if len(cross) else None,
                    "exec_cost_usd": exec_cost, "richness_usd": on.get("richness_usd", np.nan), "richness_bp": on.get("richness_bp", np.nan),
                    "roll_cost_long_usd": on["spread"] * spec["multiplier"] + exec_cost})
    return pd.DataFrame(out)


def eia_rolls(eia: pd.DataFrame, root: str) -> pd.DataFrame:
    """Monthly rolls from the EIA contract-1/contract-2 history: on each contract's roll date the c2 - c1 spread is the
    calendar spread a long pays; contract 1 in the EIA table is the front until its last trading day."""
    e = eia[eia["root"] == root].dropna(subset=["c1", "c2"]).sort_values("date")
    if e.empty:
        return pd.DataFrame()
    spec = products.spec(root)
    e = e.set_index("date")
    months = calendars.listed_months(spec, e.index.min(), e.index.max())
    exec_cost = 0.5 * spec["spread_tick"] * spec["multiplier"] + 2 * spec["fee_per_side"]
    out = []
    for m in months:
        rd = m["roll"]
        win = e[(e.index <= rd) & (e.index > rd - dt.timedelta(days=6))]
        if win.empty:
            continue
        row = win.iloc[-1]
        pre = e[(e.index <= rd - dt.timedelta(days=14)) & (e.index > rd - dt.timedelta(days=20))]
        out.append({"root": root, "delivery": m["expiry_ym"], "roll_date": win.index[-1], "c1": row["c1"], "c2": row["c2"], "spread": row["c2"] - row["c1"],
                    "spread_usd": (row["c2"] - row["c1"]) * spec["multiplier"], "drift_usd": ((row["c2"] - row["c1"]) - (pre.iloc[-1]["c2"] - pre.iloc[-1]["c1"])) * spec["multiplier"] if len(pre) else np.nan,
                    "exec_cost_usd": exec_cost, "roll_cost_long_usd": (row["c2"] - row["c1"]) * spec["multiplier"] + exec_cost, "contango": row["c2"] > row["c1"]})
    return pd.DataFrame(out)


def eia_window_path(eia: pd.DataFrame, root: str, before: int = 15, after: int = 2) -> pd.DataFrame:
    """The c2 - c1 spread by business days to the roll date, averaged over all EIA rolls (with its dispersion)."""
    e = eia[eia["root"] == root].dropna(subset=["c1", "c2"]).sort_values("date").set_index("date")
    if e.empty:
        return pd.DataFrame()
    spec = products.spec(root)
    rows = []
    for m in calendars.listed_months(spec, e.index.min(), e.index.max()):
        rd = m["roll"]
        for d, r in e[(e.index >= rd - dt.timedelta(days=int(before * 1.6) + 3)) & (e.index <= rd + dt.timedelta(days=int(after * 1.6) + 3))].iterrows():
            bd = np.busday_count(d, rd) if d <= rd else -np.busday_count(rd, d)
            if -after <= bd <= before:
                rows.append({"root": root, "delivery": m["expiry_ym"], "bd_to_roll": int(bd), "spread_usd": (r["c2"] - r["c1"]) * spec["multiplier"]})
    return pd.DataFrame(rows)


def bootstrap_mean(x: np.ndarray, n_boot: int = 2000, seed: int = 0) -> dict:
    x = np.asarray(x, dtype=float); x = x[np.isfinite(x)]
    if len(x) == 0:
        return {"mean": None, "ci": [None, None], "n": 0}
    rng = np.random.default_rng(seed)
    b = rng.choice(x, size=(n_boot, len(x)), replace=True).mean(1)
    return {"mean": float(x.mean()), "ci": [float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))], "n": int(len(x)), "sd": float(x.std(ddof=1)) if len(x) > 1 else None}


def summary(settle: pd.DataFrame, eia: pd.DataFrame, rates: pd.DataFrame, dividends: pd.DataFrame, index_prices: pd.DataFrame, roots=None) -> tuple[dict, dict]:
    """Per product: the spreads table, the roll events, the window path and the headline numbers.  Returns (json, tables)."""
    roots = roots or products.roots()
    out, tables = {}, {"spreads": [], "windows": [], "events": [], "eia_events": [], "eia_paths": []}
    for root in roots:
        spec = products.spec(root)
        sp = calendar_spreads(settle, root)
        if len(sp) and spec.get("asset_class") == "equity":
            sp = implied_financing(sp, rates, dividends, index_prices, root)
        ev = roll_events(sp, root); win = roll_windows(sp, root)
        rec = {"name": spec["name"], "asset_class": spec["asset_class"], "contracts": int(settle[settle["root"] == root]["code"].nunique()), "pair_days": int(len(sp)),
               "settlement_from": str(settle[settle["root"] == root]["date"].min()) if len(sp) else None, "settlement_to": str(settle[settle["root"] == root]["date"].max()) if len(sp) else None,
               "rolls_observed": int(len(ev)), "exec_cost_usd": 0.5 * spec["spread_tick"] * spec["multiplier"] + 2 * spec["fee_per_side"]}
        if len(ev):
            rec["events"] = ev.to_dict(orient="records")
            rec["roll_cost_long_usd"] = bootstrap_mean(ev["roll_cost_long_usd"].to_numpy())
            rec["spread_drift_10bd_usd"] = bootstrap_mean(ev["drift_usd"].to_numpy())
        if len(sp) and "richness_bp" in sp:
            front = sp.sort_values("date").groupby("date").first()          # the nearest listed pair on each date
            yr = front[front.index >= front.index.max() - dt.timedelta(days=365)]
            rec["implied_financing"] = {"richness_bp_mean": float(yr["richness_bp"].mean()), "richness_bp_ci": bootstrap_mean(yr["richness_bp"].to_numpy())["ci"],
                                        "richness_bp_p10_p90": [float(yr["richness_bp"].quantile(0.1)), float(yr["richness_bp"].quantile(0.9))],
                                        "implied_r_mean": float(yr["implied_r"].mean()), "sofr_mean": float(yr["sofr"].mean()), "div_yield_mean": float(yr["div_yield"].mean()),
                                        "richness_usd_mean": float(yr["richness_usd"].mean()), "days": int(len(yr)), "from": str(yr.index.min()), "to": str(yr.index.max())}
        if len(sp):
            near = sp.sort_values("date").groupby("date").first().reset_index()
            rec["front_spread_usd_mean"] = float(near["spread"].mean() * spec["multiplier"])
            rec["share_contango"] = float((near["spread"] > 0).mean())
            tables["spreads"].append(sp); tables["windows"].append(win); tables["events"].append(ev)
        if len(eia) and "eia_series" in spec:
            ee = eia_rolls(eia, root); pth = eia_window_path(eia, root)
            if len(ee):
                ee_recent = ee[ee["roll_date"] >= ee["roll_date"].max() - dt.timedelta(days=365)]
                by_year = ee.assign(year=[d.year for d in ee["roll_date"]]).groupby("year")["roll_cost_long_usd"].agg(["mean", "count"]).reset_index()
                rec["eia"] = {"rolls": int(len(ee)), "from": str(ee["roll_date"].min()), "to": str(ee["roll_date"].max()), "roll_cost_long_usd": bootstrap_mean(ee["roll_cost_long_usd"].to_numpy()),
                              "roll_cost_last_year_usd": bootstrap_mean(ee_recent["roll_cost_long_usd"].to_numpy()), "share_contango": float(ee["contango"].mean()),
                              "drift_usd_10bd": bootstrap_mean(ee["drift_usd"].to_numpy()), "by_year": by_year.to_dict(orient="records"),
                              "abs_roll_cost_usd": bootstrap_mean(ee["roll_cost_long_usd"].abs().to_numpy())}
                tables["eia_events"].append(ee); tables["eia_paths"].append(pth)
        out[root] = rec
    tables = {k: (pd.concat(v, ignore_index=True) if v else pd.DataFrame()) for k, v in tables.items()}
    return out, tables
