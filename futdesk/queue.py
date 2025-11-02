"""Queue-position value measured on the recorded MBO tape with shadow orders.

A shadow order is a one-off passive order inserted at the touch at a scheduled time, at a chosen place in the queue
(front, middle, back) and under a chosen matching rule (FIFO as the tape was matched, or pro-rata with TOP as SR3 is),
then carried through the recorded events: volume ahead of it is consumed by the tape's fills and cancels, it is filled
when the tape shows volume that would have reached it, and it is cancelled at the horizon.  Its value is the mark-out
of what it got filled: mid at the fill time plus tau minus the fill price, in ticks, zero when unfilled.  The value
of queue position is the difference in that value between the front and the back of the queue, with block-bootstrap
bands over insertion times.  The same pass records the outcome of every real order posted at the touch."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import book

SPECS = [(0, 0, 1), (0, 1, 1), (0, 2, 1),                  # FIFO: back, mid, front, one lot
         (1, 0, 1), (1, 0, 10), (1, 0, 50), (1, 2, 10)]     # pro-rata: back with 1/10/50 lots, TOP with 10 lots
RULE = {0: "fifo", 1: "prorata", 2: "mm"}
VARIANT = {0: "back", 1: "mid", 2: "front"}
MARKOUTS_S = (1, 10, 60)


def top_series(arr: dict) -> pd.DataFrame:
    """One row per packet: ts, best bid/ask (ticks), sizes, order counts, mid, spread."""
    top = book.replay_top(arr, 5, True)
    idx = np.asarray(top["idx"])
    bp, ap = np.asarray(top["bid_px"])[:, 0], np.asarray(top["ask_px"])[:, 0]
    df = pd.DataFrame({"event": idx, "ts": arr["ts"][idx], "bid": bp, "ask": ap, "bid_sz": np.asarray(top["bid_sz"])[:, 0], "ask_sz": np.asarray(top["ask_sz"])[:, 0],
                       "bid_ct": np.asarray(top["bid_ct"])[:, 0], "ask_ct": np.asarray(top["ask_ct"])[:, 0]})
    ok = (bp > 0) & (ap > 0) & (ap > bp)
    df["mid"] = np.where(ok, (bp + ap) / 2.0, np.nan); df["spread"] = np.where(ok, ap - bp, np.nan)
    df["mid"] = df["mid"].ffill()
    return df


def mid_at(ts_pk: np.ndarray, mid_pk: np.ndarray, t: np.ndarray) -> np.ndarray:
    k = np.searchsorted(ts_pk, t, side="right") - 1
    k = np.clip(k, 0, len(ts_pk) - 1)
    out = mid_pk[k]
    out[t > ts_pk[-1]] = np.nan          # beyond the tape
    return out


def run(arr: dict, insert_every_s: float = 1.0, horizon_s: float = 120.0, start_offset_s: float = 60.0, prorata_min: int = 2, top_max: int = 1000000,
        specs=SPECS, mm: bool = True, mm_limit: int = 5, mm_size: int = 1, end_ns: int | None = None) -> dict:
    if not book.HAVE_EXT:
        raise RuntimeError("the compiled replay is needed for the shadow study: python build_ext.py build_ext --inplace")
    t0, t1 = int(arr["ts"][0]), int(arr["ts"][-1]) if end_ns is None else int(end_ns)
    out = book._ext.run_shadows(arr["ts"], arr["action"], arr["side"], arr["price"], arr["size"], arr["order_id"], arr["flags"], list(specs),
                                int(insert_every_s * 1e9), int(horizon_s * 1e9), t0 + int(start_offset_s * 1e9), t1, prorata_min, top_max, True, mm, mm_limit, mm_size, 0)
    sh = pd.DataFrame({k: np.asarray(v) for k, v in out["shadows"].items()})
    fi = pd.DataFrame({k: np.asarray(v) for k, v in out["fills"].items()})
    ro = pd.DataFrame({k: np.asarray(v) for k, v in out["real_orders"].items()})
    mmf = pd.DataFrame({k: np.asarray(v) for k, v in out["mm"].items()})
    return {"shadows": sh, "fills": fi, "real_orders": ro, "mm_fills": mmf, "n_insertions": int(out["n_insertions"])}


def fill_markouts(res: dict, arr: dict, top: pd.DataFrame, markouts_s=MARKOUTS_S) -> pd.DataFrame:
    """Per shadow fill: fill time, price, size and the mark-outs in ticks (positive = the fill made money)."""
    fi = res["fills"].copy(); sh = res["shadows"]
    fi["ts"] = arr["ts"][fi["event"].to_numpy()]
    fi = fi.merge(sh[["id", "side", "rule", "variant", "size", "t_insert", "queue_at_insert"]], on="id", how="left")
    ts_pk, mid_pk = top["ts"].to_numpy(), top["mid"].to_numpy()
    fi["mid_at_fill"] = mid_at(ts_pk, mid_pk, fi["ts"].to_numpy())
    sign = fi["side"].to_numpy().astype(float)
    for tau in markouts_s:
        m = mid_at(ts_pk, mid_pk, fi["ts"].to_numpy() + int(tau * 1e9))
        fi[f"mo_{tau}s"] = sign * (m - fi["price"].to_numpy())
    fi["half_spread"] = sign * (fi["mid_at_fill"] - fi["price"])
    fi["time_to_fill_s"] = (fi["ts"] - fi["t_insert"]) / 1e9
    return fi


def _block_ids(t_ns: np.ndarray, block_s: float) -> np.ndarray:
    return ((t_ns - t_ns.min()) // int(block_s * 1e9)).astype(np.int64)


def summarise(res: dict, fills: pd.DataFrame, tau: int = 10, block_s: float = 300.0, n_boot: int = 1000, seed: int = 0, session_filter=None) -> dict:
    """Value per lot posted by (rule, variant, size) with bootstrap bands; the queue-position value under each rule."""
    sh = res["shadows"]; sh = sh[sh["rule"] != 2].copy()
    if session_filter is not None:
        sh = sh[session_filter(sh["t_insert"].to_numpy())]
    fi = fills[fills["id"].isin(sh["id"])]
    col = f"mo_{tau}s"
    fi = fi.dropna(subset=[col])
    sh["block"] = _block_ids(sh["t_insert"].to_numpy(), block_s)
    fi = fi.merge(sh[["id", "block"]], on="id")
    fi["pnl"] = fi["qty"] * fi[col]
    groups = sh.groupby(["rule", "variant", "size"])
    rng = np.random.default_rng(seed)
    blocks = np.sort(sh["block"].unique()); nb = len(blocks)
    draws = rng.integers(0, nb, size=(n_boot, nb))
    out = {"groups": {}, "tau_s": tau, "block_s": block_s, "n_boot": n_boot, "n_shadows": int(len(sh)), "n_blocks": int(nb)}
    per_group_block = {}
    for key, g in groups:
        f = fi[fi["id"].isin(g["id"])]
        posted = g.groupby("block")["size"].sum().reindex(blocks, fill_value=0).to_numpy(dtype=float)
        pnl = f.groupby("block")["pnl"].sum().reindex(blocks, fill_value=0).to_numpy(dtype=float)
        filled = f.groupby("block")["qty"].sum().reindex(blocks, fill_value=0).to_numpy(dtype=float)
        per_group_block[key] = (posted, pnl, filled)
        v = pnl.sum() / posted.sum() if posted.sum() else np.nan
        boot = (pnl[draws].sum(1) / np.maximum(posted[draws].sum(1), 1))
        any_fill = g["filled"].gt(0).mean()
        name = f"{RULE[key[0]]}_{VARIANT[key[1]]}_{key[2]}"
        out["groups"][name] = {"rule": RULE[key[0]], "variant": VARIANT[key[1]], "size": int(key[2]), "n": int(len(g)), "lots_posted": float(posted.sum()), "lots_filled": float(filled.sum()),
                               "fill_share": float(filled.sum() / posted.sum()) if posted.sum() else None, "p_any_fill": float(any_fill),
                               "median_time_to_fill_s": float(f["time_to_fill_s"].median()) if len(f) else None,
                               "markout_per_filled_lot": float(pnl.sum() / filled.sum()) if filled.sum() else None,
                               "half_spread_per_filled_lot": float((f["qty"] * f["half_spread"]).sum() / filled.sum()) if filled.sum() else None,
                               "value_per_lot": float(v), "ci": [float(np.nanpercentile(boot, 2.5)), float(np.nanpercentile(boot, 97.5))]}

    def diff(k1, k2):
        if k1 not in per_group_block or k2 not in per_group_block:
            return None
        p1, n1, _ = per_group_block[k1]; p2, n2, _ = per_group_block[k2]
        d = n1.sum() / p1.sum() - n2.sum() / p2.sum()
        b = n1[draws].sum(1) / np.maximum(p1[draws].sum(1), 1) - n2[draws].sum(1) / np.maximum(p2[draws].sum(1), 1)
        return {"ticks": float(d), "ci": [float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))]}
    out["queue_value_fifo"] = diff((0, 2, 1), (0, 0, 1))                 # front minus back, one lot
    out["queue_value_fifo_mid"] = diff((0, 1, 1), (0, 0, 1))
    out["queue_value_prorata_top"] = diff((1, 2, 10), (1, 0, 10))         # TOP minus back, ten lots
    out["prorata_size_effect"] = diff((1, 0, 50), (1, 0, 1))              # per lot, fifty lots against one
    out["fifo_vs_prorata_back"] = diff((0, 0, 1), (1, 0, 1))
    return out


def real_order_outcomes(res: dict, arr: dict, top: pd.DataFrame, tau: int = 10) -> dict:
    """Every real order added at the touch: fill probability and time to outcome by queue position (volume ahead / level size)."""
    ro = res["real_orders"].copy()
    if ro.empty:
        return {}
    ro["life_s"] = (ro["t_end"] - ro["t_add"]) / 1e9
    ro["ahead"] = ro["ahead"].clip(lower=0)
    lv = ro["ahead"] + ro["size"]
    ro["pos_frac"] = np.where(lv > 0, ro["ahead"] / lv, 0.0)
    edges = [0, 1, 5, 10, 20, 50, 100, 1000000]
    ro["ahead_bucket"] = pd.cut(ro["ahead"], edges, right=False, labels=[f"{edges[i]}-{edges[i+1]-1}" if edges[i+1] < 1000000 else f"{edges[i]}+" for i in range(len(edges) - 1)])
    filled = ro[ro["outcome"] == 1].copy()
    ts_pk, mid_pk = top["ts"].to_numpy(), top["mid"].to_numpy()
    if len(filled):
        m = mid_at(ts_pk, mid_pk, filled["t_end"].to_numpy() + int(tau * 1e9))
        filled["mo"] = filled["side"] * (m - filled["price"])
    by = ro.groupby("ahead_bucket", observed=True).agg(n=("outcome", "size"), p_fill=("outcome", lambda s: float((s == 1).mean())), median_life_s=("life_s", "median"), mean_size=("size", "mean"))
    if len(filled):
        by = by.join(filled.groupby("ahead_bucket", observed=True)["mo"].mean().rename(f"markout_{tau}s"))
    return {"n_orders": int(len(ro)), "p_fill": float((ro["outcome"] == 1).mean()), "p_cancel": float((ro["outcome"] == 0).mean()),
            "median_life_s": float(ro["life_s"].median()), "median_life_filled_s": float(filled["life_s"].median()) if len(filled) else None,
            "median_life_cancelled_s": float(ro.loc[ro["outcome"] == 0, "life_s"].median()), "by_ahead": by.reset_index().to_dict(orient="records"),
            "mean_size": float(ro["size"].mean()), "share_one_lot": float((ro["size"] == 1).mean())}


def book_stats(arr: dict, top: pd.DataFrame, tick_value: float) -> dict:
    """Time-weighted spread and touch depth, event and trade counts, trade sizes, by hour of the session (UTC)."""
    t = top.copy()
    t["dt"] = np.append(np.diff(t["ts"].to_numpy()), 0) / 1e9
    t = t[t["dt"] > 0]
    t["hour"] = pd.to_datetime(t["ts"], unit="ns", utc=True).dt.hour
    w = t["dt"]
    tw = lambda c: float((t[c] * w).sum() / w.sum())
    trades = arr["action"] == 5
    tsz = arr["size"][trades]
    by_hour = t.groupby("hour").apply(lambda g: pd.Series({"spread_ticks": (g["spread"] * g["dt"]).sum() / g["dt"].sum(), "touch_depth": ((g["bid_sz"] + g["ask_sz"]) / 2 * g["dt"]).sum() / g["dt"].sum(),
                                                            "touch_orders": ((g["bid_ct"] + g["ask_ct"]) / 2 * g["dt"]).sum() / g["dt"].sum(), "seconds": g["dt"].sum()}), include_groups=False)
    ev_hour = pd.Series(pd.to_datetime(arr["ts"], unit="ns", utc=True).hour).value_counts().sort_index()
    tr_hour = pd.Series(pd.to_datetime(arr["ts"][trades], unit="ns", utc=True).hour).value_counts().sort_index()
    by_hour["events"] = ev_hour.reindex(by_hour.index).fillna(0).astype(int); by_hour["trades"] = tr_hour.reindex(by_hour.index).fillna(0).astype(int)
    return {"events": int(len(arr["action"])), "trades": int(trades.sum()), "traded_lots": int(tsz.sum()), "orders_added": int((arr["action"] == 1).sum()), "cancels": int((arr["action"] == 2).sum()),
            "modifies": int((arr["action"] == 3).sum()), "spread_ticks_tw": tw("spread"), "touch_depth_tw": float(((t["bid_sz"] + t["ask_sz"]) / 2 * w).sum() / w.sum()),
            "touch_orders_tw": float(((t["bid_ct"] + t["ask_ct"]) / 2 * w).sum() / w.sum()), "share_time_one_tick": float((w[t["spread"] == 1]).sum() / w.sum()),
            "trade_size_mean": float(tsz.mean()) if len(tsz) else None, "trade_size_p50": float(np.median(tsz)) if len(tsz) else None, "trade_size_p99": float(np.percentile(tsz, 99)) if len(tsz) else None,
            "tick_value_usd": tick_value, "by_hour": by_hour.reset_index().to_dict(orient="records"),
            "span_hours": float((arr["ts"][-1] - arr["ts"][0]) / 3.6e12), "start": pd.Timestamp(int(arr["ts"][0]), unit="ns", tz="UTC").isoformat(), "end": pd.Timestamp(int(arr["ts"][-1]), unit="ns", tz="UTC").isoformat()}
