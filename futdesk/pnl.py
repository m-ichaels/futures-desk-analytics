"""P&L attribution.

Daily, for the stated book (configs/book.json) on settlements: the held contract of each position rolls on the
product's roll date at that day's settlement, so the daily settlement variation splits exactly into a price component
(the front-month price change, roll jump included) and a roll component (minus the calendar spread paid on roll days);
fees and the half-tick crossing cost at each roll; the financing of the SPAN-style initial margin at SOFR; and the
core/hedge split of every component.  Identity: variation = price + roll, total = variation + execution + financing.

Intraday, for the passive market maker replayed on the MBO tape: spread capture (fills against the mid at the time),
inventory (the mark-to-mid of the position between fills), hedge cost (crossing the spread to flatten) and fees.
Identity: cash + final inventory at the closing mid = spread capture + inventory + hedge + 0, fees separate."""
from __future__ import annotations

import datetime as dt
import json
import os

import numpy as np
import pandas as pd

from . import calendars, data, margin, products


def load_book(path: str | None = None) -> dict:
    path = path or os.path.join(data.ROOT, "configs", "book.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def held_contract(spec: dict, on: dt.date) -> dict:
    """The contract held on the date: the nearest one whose roll date is on or after the date (the switch happens at
    the roll date's settlement)."""
    for d in calendars.listed_months(spec, on - dt.timedelta(days=5), on + dt.timedelta(days=400)):
        if d["roll"] >= on:
            return d
    raise ValueError("no contract")


def _price_table(settle: pd.DataFrame, root: str) -> pd.DataFrame:
    s = settle[settle["root"] == root]
    return s.pivot(index="date", columns="code", values="close").sort_index()


def daily_book(book: dict, settle: pd.DataFrame, rates: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One row per position per business day with the components in dollars; and the book summary."""
    start = dt.date.fromisoformat(book["start"]); end = dt.date.fromisoformat(book["end"])
    dates = [d for d in calendars.business_days(start, end) if d in set(settle["date"])]
    r = rates.set_index("date")["SOFR"].sort_index() / 100.0
    tables = {p["root"]: _price_table(settle, p["root"]) for p in book["positions"]}
    rows = []
    # the effective window: every position needs its held contract priced on the day and on the previous settlement day
    first_ok = start
    for p in book["positions"]:
        spec = products.spec(p["root"]); tab = tables[p["root"]]
        ok = end
        for d in dates:
            hc = held_contract(spec, d); c = calendars.contract_code(p["root"], hc["year"], hc["month"])
            prev = tab.index[tab.index < d]
            if d in tab.index and c in tab.columns and len(prev) and np.isfinite(tab.loc[d, c]) and np.isfinite(tab.loc[prev[-1], c]):
                ok = d; break
        first_ok = max(first_ok, ok)
    dates = [d for d in dates if d >= first_ok]
    for p in book["positions"]:
        root, qty = p["root"], p["qty"]; spec = products.spec(root); mult = spec["multiplier"]; tab = tables[root]
        prev_code = None; prev_date = None
        for d in dates:
            hc = held_contract(spec, d); code = calendars.contract_code(root, hc["year"], hc["month"])
            if d not in tab.index or code not in tab.columns or np.isnan(tab.loc[d, code]):
                continue
            px = float(tab.loc[d, code])
            prevs = tab.index[tab.index < d]
            if not len(prevs):
                prev_code, prev_date = code, d; continue
            pd_ = prevs[-1]
            px_prev_same = tab.loc[pd_, code] if code in tab.columns else np.nan
            if prev_code is None:
                prev_code = code
            px_prev_old = tab.loc[pd_, prev_code] if prev_code in tab.columns else np.nan
            if np.isnan(px_prev_same) or np.isnan(px_prev_old):
                prev_code, prev_date = code, d; continue
            variation = qty * mult * (px - px_prev_same)
            price = qty * mult * (px - px_prev_old)
            rolled = code != prev_code
            roll_comp = -qty * mult * (px_prev_same - px_prev_old) if rolled else 0.0
            exec_cost = -(abs(qty) * (0.5 * spec["spread_tick"] * mult + 2 * spec["fee_per_side"])) if rolled else 0.0
            rows.append({"date": d, "root": root, "code": code, "qty": qty, "role": p.get("role", "core"), "pair": p.get("pair", root), "settle": px, "variation": variation,
                         "price": price, "roll": roll_comp, "execution": exec_cost, "rolled": rolled, "spread_paid": (px_prev_same - px_prev_old) if rolled else 0.0,
                         "notional": abs(qty) * mult * px})
            prev_code, prev_date = code, d
    df = pd.DataFrame(rows)
    if df.empty:
        return df, {"error": "no settlement data inside the book window"}
    # margin and financing per day on the book's positions (calendar days to the next row's date)
    days = sorted(df["date"].unique())
    fin = []
    for i, d in enumerate(days):
        pos = {(x["root"], x["code"]): int(x["qty"]) for x in df[df["date"] == d][["root", "code", "qty"]].to_dict(orient="records")}
        m = margin.book_margin(pos)
        nxt = days[i + 1] if i + 1 < len(days) else d + dt.timedelta(days=1)
        ndays = (nxt - d).days
        sofr = float(r[:d].iloc[-1]) if len(r[:d]) else np.nan
        spread = products.config()["financing"]["collateral_yield_spread_bp"] / 1e4
        fin.append({"date": d, "initial_margin": m["initial_margin"], "outright_margin": m["outright_sum"], "credit": sum(m["intercommodity_credit"].values()),
                    "sofr": sofr, "financing": -m["initial_margin"] * (sofr - spread) * ndays / 360.0, "calendar_days": ndays})
    fin = pd.DataFrame(fin)
    df["total_ex_financing"] = df["variation"] + df["execution"]
    by_role = df.groupby("role")[["variation", "price", "roll", "execution"]].sum()
    by_root = df.groupby("root")[["variation", "price", "roll", "execution"]].sum()
    by_root["rolls"] = df.groupby("root")["rolled"].sum()
    daily = df.groupby("date")[["variation", "price", "roll", "execution"]].sum().join(fin.set_index("date")[["financing", "initial_margin", "sofr"]])
    daily["total"] = daily["variation"] + daily["execution"] + daily["financing"]
    tot = daily[["variation", "price", "roll", "execution", "financing", "total"]].sum()
    var_daily = daily["variation"]
    summary = {"from": str(days[0]), "to": str(days[-1]), "days": int(len(days)), "positions": book["positions"],
               "components_usd": {k: float(tot[k]) for k in tot.index}, "identity_gap_usd": float(abs(tot["price"] + tot["roll"] - tot["variation"])),
               "by_role_usd": by_role.round(2).to_dict(orient="index"), "by_root_usd": by_root.round(2).to_dict(orient="index"),
               "avg_initial_margin_usd": float(daily["initial_margin"].mean()), "avg_notional_usd": float(df.groupby("date")["notional"].sum().mean()),
               "variation_daily_sd_usd": float(var_daily.std()), "worst_day_usd": float(var_daily.min()), "worst_day": str(var_daily.idxmin()), "best_day_usd": float(var_daily.max()),
               "max_drawdown_usd": float((daily["total"].cumsum() - daily["total"].cumsum().cummax()).min()),
               "rolls": df[df["rolled"]][["date", "root", "code", "qty", "spread_paid", "roll", "execution"]].assign(date=lambda x: x["date"].astype(str)).to_dict(orient="records"),
               "financing_share_of_gross_pnl": float(abs(tot["financing"]) / daily["variation"].abs().sum()) if daily["variation"].abs().sum() else None}
    df["date"] = df["date"].astype(str)
    return df.merge(fin.assign(date=fin["date"].astype(str))[["date", "initial_margin", "financing", "sofr"]], on="date", how="left"), summary


def intraday_mm(mm_fills: pd.DataFrame, top: pd.DataFrame, spec: dict) -> tuple[pd.DataFrame, dict]:
    """The market maker's fills on the tape -> the attribution in ticks and dollars, with the reconciliation."""
    f = mm_fills.copy()
    if f.empty:
        return f, {}
    ts_pk, mid_pk = top["ts"].to_numpy(), top["mid"].to_numpy()
    from .queue import mid_at
    f["mid"] = mid_at(ts_pk, mid_pk, f["t"].to_numpy())
    f = f.dropna(subset=["mid"]).sort_values("t").reset_index(drop=True)
    sgn = f["side"].astype(float); q = f["qty"].astype(float)
    f["edge_ticks"] = sgn * (f["mid"] - f["price"]) * q                       # positive: bought below / sold above the mid
    f["inv_after"] = (sgn * q).cumsum()
    mid_next = np.append(f["mid"].to_numpy()[1:], mid_pk[-1])
    f["inventory_ticks"] = f["inv_after"] * (mid_next - f["mid"])            # the position held until the next fill, marked to mid
    spread_capture = float(f.loc[f["kind"] == 0, "edge_ticks"].sum()); hedge = float(f.loc[f["kind"] == 1, "edge_ticks"].sum()); inventory = float(f["inventory_ticks"].sum())
    cash = float(-(sgn * q * f["price"]).sum()); inv_end = float(f["inv_after"].iloc[-1]); total = cash + inv_end * float(mid_pk[-1])
    lots = float(q.sum()); fees = -lots * spec["fee_per_side"]
    tv = spec["tick_value"]
    hours = float((f["t"].iloc[-1] - f["t"].iloc[0]) / 3.6e12)
    out = {"fills": int(len(f)), "passive_fills": int((f["kind"] == 0).sum()), "hedges": int((f["kind"] == 1).sum()), "lots": lots, "hedge_lots": float(q[f["kind"] == 1].sum()),
           "ticks": {"spread_capture": spread_capture, "inventory": inventory, "hedge": hedge, "total": total, "identity_gap": total - (spread_capture + inventory + hedge)},
           "usd": {"spread_capture": spread_capture * tv, "inventory": inventory * tv, "hedge": hedge * tv, "fees": fees, "total_after_fees": total * tv + fees},
           "per_passive_lot_usd": {"spread_capture": spread_capture * tv / max(1, (f["kind"] == 0).sum()), "inventory": inventory * tv / max(1, (f["kind"] == 0).sum()),
                                   "hedge": hedge * tv / max(1, (f["kind"] == 0).sum()), "fees": fees / max(1, (f["kind"] == 0).sum()), "total_after_fees": (total * tv + fees) / max(1, (f["kind"] == 0).sum())},
           "max_abs_inventory": float(f["inv_after"].abs().max()), "hours": hours, "inv_end": inv_end}
    return f, out
