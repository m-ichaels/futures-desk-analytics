"""The pipeline: tapes -> book validation -> queue study -> market-maker attribution; settlements -> rolls -> the stated
book's attribution; the monitor harness; results/run.json, the derived tables and the DuckDB store."""
from __future__ import annotations

import datetime as dt
import os
import time

import numpy as np
import pandas as pd

from . import book, calendars, data, matching, monitor, pnl, products, queue, roll


def validate_book(arr: dict, mbp_path: str, tick: float, max_packets: int | None = None) -> dict:
    """Our rebuilt book against Databento's MBP-10 at every packet end (last state per sequence number)."""
    top = book.replay_top(arr, 10, True)
    seq = arr["sequence"][np.asarray(top["idx"])]
    pos = pd.Series(np.arange(len(seq)), index=seq); pos = pos[~pos.index.duplicated(keep="last")]
    mbp = pd.read_parquet(mbp_path)
    mbp = mbp[mbp["flags"] & 128 > 0].drop_duplicates("sequence", keep="last")
    m = mbp[mbp["sequence"].isin(pos.index)]
    if max_packets:
        m = m.iloc[:max_packets]
    k = pos.loc[m["sequence"].values].values
    tick_units = int(round(tick * data.PRICE_SCALE)); bad = 0; fields = 0
    for lvl in range(10):
        for f, div in (("bid_px", tick_units), ("bid_sz", 1), ("bid_ct", 1), ("ask_px", tick_units), ("ask_sz", 1), ("ask_ct", 1)):
            v = m[f"{f}_{lvl:02d}"].values // div
            bad += int((v != np.asarray(top[f])[k, lvl]).sum()); fields += len(v)
    return {"packets": int(len(m)), "fields": int(fields), "mismatches": int(bad), "match_rate": 1 - bad / max(1, fields)}


def matching_examples() -> dict:
    """The allocation rules on one level (100/50/30/1 lots resting, 60 lots incoming) and one implied-spread scenario."""
    def level():
        return [matching.Order(1, 1, 100, 100, top=True), matching.Order(2, 1, 100, 50), matching.Order(3, 1, 100, 30), matching.Order(4, 1, 100, 1)]
    out = {"resting": [100, 50, 30, 1], "incoming": 60, "rules": {}}
    for name, rule in (("FIFO", matching.Rule.fifo()), ("pro-rata, 2-lot minimum, no TOP", matching.Rule("C", 0.0, False, 1, 10**9, 2)),
                       ("Allocation: TOP (max 10) + pro-rata + FIFO leftover", matching.Rule.allocation(2, top_max=10)), ("split 40 % FIFO / 60 % pro-rata", matching.Rule.split(0.4, 2, False))):
        out["rules"][name] = {str(o.id): q for o, q in matching.allocate(level(), 60, rule)}
    c = matching.SpreadComplex()
    c.submit("leg1", matching.Order(1, 1, 1000, 10)); c.submit("leg1", matching.Order(2, -1, 1002, 10))
    c.submit("leg2", matching.Order(3, 1, 990, 4)); c.submit("leg2", matching.Order(4, -1, 993, 6))
    before = c.quotes()
    fills = c.submit("spread", matching.Order(6, -1, 7, 2))
    out["implied"] = {"before": before, "sell_2_spread_at_7": [f.__dict__ for f in fills], "leg_fills": [f.__dict__ for f in c.fills if f.via == "implied-leg"], "after": c.quotes()}
    return out


def session_masks(arr: dict):
    """RTH = 13:30-20:00 UTC (08:30-15:00 Chicago); the rest is the overnight Globex session."""
    def rth(t):
        h = ((t // 3_600_000_000_000) % 24).astype(int); mnt = ((t // 60_000_000_000) % 60).astype(int)
        return ((h > 13) | ((h == 13) & (mnt >= 30))) & (h < 20)
    return {"rth": rth, "overnight": lambda t: ~rth(t)}


def run(fast: bool = False, tapes: list[str] | None = None) -> dict:
    t0 = time.monotonic()
    data.ensure_dirs()
    n_boot = 300 if fast else 1000
    run_json = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "fast": fast, "ext": book.HAVE_EXT, "tapes": [], "queue": {}, "mm": {}}
    con = data.connect()
    # ---- tapes
    files = tapes or data.mbo_files()
    mm_cfg = pnl.load_book().get("intraday", {})
    for path in files:
        df = data.load_mbo(path)
        for sym in sorted(df["symbol"].unique()):
            root = calendars.parse_code(sym, products.roots())[0]; spec = products.spec(root)
            sub = df[df["symbol"] == sym]
            arr = data.mbo_arrays(sub, spec["tick"])
            live = (arr["flags"] & 32) == 0
            start_ns = int(arr["ts"][live][0]) if live.any() else int(arr["ts"][0])
            info = {"file": os.path.basename(path), "symbol": sym, "root": root, "events": int(len(sub)), "start": pd.Timestamp(start_ns, unit="ns", tz="UTC").isoformat(),
                    "end": pd.Timestamp(int(arr["ts"][-1]), unit="ns", tz="UTC").isoformat(), "hours": float((arr["ts"][-1] - start_ns) / 3.6e12)}
            mbp = path.replace("mbo_", "book_").replace(".parquet", ".mbp10.parquet")
            if os.path.exists(mbp):
                info["validation"] = validate_book(arr, mbp, spec["tick"], max_packets=200000 if fast else None)
            run_json["tapes"].append(info)
            top = queue.top_series(arr)
            top_live = top[top["ts"] >= start_ns]
            arr_stats = {k: v[live] for k, v in arr.items()}
            stats = queue.book_stats(arr_stats, top_live, spec["tick_value"])
            if not book.HAVE_EXT:
                run_json["queue"][sym] = {"book": stats, "error": "compiled replay not built"}
                continue
            mp = products.matching_params(root)
            res = queue.run(arr, insert_every_s=5.0 if fast else 1.0, horizon_s=120.0, start_offset_s=(start_ns - int(arr["ts"][0])) / 1e9 + 60.0,
                            prorata_min=2, top_max=10**6, mm=(root == mm_cfg.get("root", "ES")), mm_limit=int(mm_cfg.get("inventory_limit", 5)), mm_size=int(mm_cfg.get("size", 1)))
            fills = queue.fill_markouts(res, arr, top)
            q = {"book": stats, "matching_on_exchange": mp["algo"], "n_insertions": res["n_insertions"], "n_shadows": int((res["shadows"]["rule"] != 2).sum()), "n_shadow_fills": int(len(fills)),
                 "tau_10s": queue.summarise(res, fills, tau=10, n_boot=n_boot), "tau_1s": queue.summarise(res, fills, tau=1, n_boot=n_boot // 2), "tau_60s": queue.summarise(res, fills, tau=60, n_boot=n_boot // 2)}
            masks = session_masks(arr)
            for name, fn in masks.items():
                sh = res["shadows"]
                if fn(sh["t_insert"].to_numpy()).sum() > 500:
                    q[f"tau_10s_{name}"] = queue.summarise(res, fills, tau=10, n_boot=n_boot // 2, session_filter=fn)
            q["real_orders"] = queue.real_order_outcomes(res, arr, top)
            run_json["queue"][sym] = q
            # store
            sh = res["shadows"].copy(); sh["symbol"] = sym; sh = sh[sh["rule"] != 2]
            sh.to_parquet(os.path.join(data.DER, f"shadow_{sym}.parquet"), index=False)
            f2 = fills[["id", "ts", "price", "qty", "side", "rule", "variant", "size", "half_spread", "mo_1s", "mo_10s", "mo_60s", "time_to_fill_s"]].copy(); f2["symbol"] = sym
            f2.to_parquet(os.path.join(data.DER, f"shadow_fills_{sym}.parquet"), index=False)
            ro = res["real_orders"].copy(); ro["symbol"] = sym
            for name, d in (("shadows", sh), ("shadow_fills", f2), ("real_orders", ro)):
                con.execute(f"CREATE OR REPLACE TABLE {name}_{sym.lower()} AS SELECT * FROM d")
            t1 = top_live.iloc[::max(1, len(top_live) // 200000)].copy(); t1["symbol"] = sym
            con.execute(f"CREATE OR REPLACE TABLE top_{sym.lower()} AS SELECT * FROM t1")
            if len(res["mm_fills"]):
                mmf, mm_out = pnl.intraday_mm(res["mm_fills"], top, spec)
                mm_out["symbol"] = sym; mm_out["config"] = mm_cfg
                run_json["mm"][sym] = mm_out
                mmf["symbol"] = sym; mmf.to_parquet(os.path.join(data.DER, "mm_fills.parquet"), index=False)
                con.execute("CREATE OR REPLACE TABLE mm_fills AS SELECT * FROM mmf")
    # ---- rolls
    settle = data.load_settlements(); eia = data.load_eia_front(); rates = data.load_rates(); divs = data.load_dividends(); ip = data.load_index_prices()
    roll_json, tabs = roll.summary(settle, eia, rates, divs, ip)
    run_json["roll"] = roll_json
    for name, d in tabs.items():
        if len(d):
            d2 = d.copy()
            for c in d2.columns:
                if d2[c].dtype == object and len(d2) and isinstance(d2[c].dropna().iloc[0] if len(d2[c].dropna()) else None, dt.date):
                    d2[c] = d2[c].astype(str)
            d2.to_parquet(os.path.join(data.DER, f"roll_{name}.parquet"), index=False)
            con.execute(f"CREATE OR REPLACE TABLE roll_{name} AS SELECT * FROM d2")
    # ---- the stated book
    bk = pnl.load_book()
    daily, summ = pnl.daily_book(bk, settle, rates)
    run_json["book"] = summ
    if len(daily):
        daily.to_parquet(os.path.join(data.DER, "book_daily.parquet"), index=False)
        con.execute("CREATE OR REPLACE TABLE book_daily AS SELECT * FROM daily")
    # ---- the monitor harness
    ref = {}
    for p in bk["positions"]:
        x = settle[settle["root"] == p["root"]].sort_values("date")
        if len(x):
            last = x[x["code"] == x[x["date"] == x["date"].max()]["code"].iloc[0]]
            ret = np.log(last["close"]).diff().dropna()
            ref[p["root"]] = (float(last["close"].iloc[-1]), float(ret.std()) if len(ret) > 10 else 0.01)
        else:
            ref[p["root"]] = (100.0, 0.01)
    es_path = None
    es_tables = [r[0] for r in con.execute("SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'top_es%'").fetchall()]
    if es_tables:
        es_path = con.execute(f"SELECT mid FROM {es_tables[0]} WHERE mid IS NOT NULL ORDER BY ts").df()["mid"].to_numpy()
    h = monitor.harness(bk, ref, es_path, dt.date.fromisoformat(bk["end"]), n_days=8 if fast else 40, seed=1)
    alerts = pd.DataFrame([{"date": x["date"], "fault_day": x["fault_day"], **a} for x in h["days"] for a in x["alerts"]])
    faults = pd.DataFrame([{"date": x["date"], **f} for x in h["days"] for f in x["faults"]])
    run_json["monitor"] = {k: v for k, v in h.items() if k != "days"}
    run_json["monitor"]["limits"] = monitor.DEFAULT_LIMITS
    if len(alerts):
        alerts.to_parquet(os.path.join(data.DER, "monitor_alerts.parquet"), index=False); con.execute("CREATE OR REPLACE TABLE monitor_alerts AS SELECT * FROM alerts")
    if len(faults):
        faults.to_parquet(os.path.join(data.DER, "monitor_faults.parquet"), index=False); con.execute("CREATE OR REPLACE TABLE monitor_faults AS SELECT * FROM faults")
    run_json["matching"] = matching_examples()
    con.close()
    run_json["seconds"] = time.monotonic() - t0
    data.write_json(run_json, os.path.join(data.RESULTS, "run.json"))
    return run_json
