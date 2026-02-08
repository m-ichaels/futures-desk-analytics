"""The attribution identities, the roll arithmetic, the margin, and every fault kind against the monitor."""
import datetime as dt

import numpy as np
import pandas as pd

from futdesk import calendars, margin, monitor, pnl, products, roll


def synthetic_settlements(root="ES", start=dt.date(2026, 8, 3), end=dt.date(2026, 9, 25), seed=0):
    """Two contracts (U26, Z26) with a random walk and a fixed 60-point calendar spread."""
    rng = np.random.default_rng(seed)
    rows = []; px = 6000.0
    for d in calendars.business_days(start, end):
        px += rng.normal(0, 30)
        for code, y, m, off in (("ESU26", 2026, 9, 0.0), ("ESZ26", 2026, 12, 60.0)):
            rows.append({"date": d, "root": root, "code": code, "year": y, "month": m, "close": round(px + off, 2), "volume": 1000, "source": "test"})
    return pd.DataFrame(rows)


def test_daily_book_identity_and_roll_component():
    settle = synthetic_settlements()
    rates = pd.DataFrame({"date": [dt.date(2026, 1, 1)], "SOFR": [4.0], "EFFR": [4.0]})
    book = {"start": "2026-08-03", "end": "2026-09-25", "cash_usd": 1e6, "positions": [{"root": "ES", "qty": 10, "role": "core", "pair": "x"}]}
    df, s = pnl.daily_book(book, settle, rates)
    assert s["identity_gap_usd"] < 1e-6
    assert len(s["rolls"]) == 1 and s["rolls"][0]["code"] == "ESZ26" and abs(s["rolls"][0]["spread_paid"] - 60.0) < 1e-9
    assert abs(s["components_usd"]["roll"] - (-10 * 50 * 60.0)) < 1e-6                     # the long pays the contango
    c = s["components_usd"]
    assert abs(c["total"] - (c["variation"] + c["execution"] + c["financing"])) < 1e-6
    assert c["financing"] < 0 and c["execution"] == -10 * (0.5 * 0.05 * 50 + 2 * products.spec("ES")["fee_per_side"])
    # the held contract switches at the roll date's settlement: the first day on ESZ26 is the business day after 11 Sep
    first_z = df[df["code"] == "ESZ26"]["date"].min()
    assert first_z == "2026-09-14"


def test_intraday_mm_identity():
    ts = np.arange(0, 1000, dtype=np.int64) * 10**9
    top = pd.DataFrame({"ts": ts, "mid": 100.0 + np.sin(ts / 1e11) * 3})
    rng = np.random.default_rng(1)
    n = 200
    fills = pd.DataFrame({"t": np.sort(rng.integers(0, 990, n)) * 10**9, "side": rng.choice([-1, 1], n).astype(np.int8), "price": rng.integers(98, 103, n).astype(np.int64),
                          "qty": rng.integers(1, 4, n).astype(np.int32), "kind": rng.choice([0, 1], n, p=[0.9, 0.1]).astype(np.int8)})
    f, out = pnl.intraday_mm(fills, top, products.spec("ES"))
    assert abs(out["ticks"]["identity_gap"]) < 1e-9
    assert out["fills"] == n and out["usd"]["fees"] == -f["qty"].sum() * products.spec("ES")["fee_per_side"]


def test_margin_credits_and_calendar_charge():
    m = margin.book_margin({("ES", "ESU26"): 40, ("NQ", "NQU26"): -20})
    assert m["scan"]["ES"] == 40 * 18000 and m["scan"]["NQ"] == 20 * 27000
    assert abs(m["intercommodity_credit"]["ES/NQ"] - 0.7 * min(40 * 18000, 20 * 27000)) < 1e-9
    assert m["initial_margin"] == m["outright_sum"] - m["intercommodity_credit"]["ES/NQ"]
    m2 = margin.book_margin({("ES", "ESU26"): 10, ("ES", "ESZ26"): -10})
    assert m2["scan"]["ES"] == 0 and abs(m2["calendar_charge"]["ES"] - 10 * 18000 * 0.06) < 1e-9


def test_calendar_spreads_and_implied_financing():
    settle = synthetic_settlements()
    sp = roll.calendar_spreads(settle, "ES")
    assert set(sp["near"]) == {"ESU26"} and set(sp["far"]) == {"ESZ26"} and np.allclose(sp["spread"], 60.0)
    assert sp["days_between"].iloc[0] == (dt.date(2026, 12, 18) - dt.date(2026, 9, 18)).days
    rates = pd.DataFrame({"date": [dt.date(2026, 1, 1)], "SOFR": [4.0], "EFFR": [4.0]})
    divs = pd.DataFrame({"symbol": ["SPY"] * 4, "date": [dt.date(2026, m, 15) for m in (1, 3, 5, 7)], "dividend": [1.5] * 4})
    px = pd.DataFrame({"symbol": ["SPY"], "date": [dt.date(2026, 1, 2)], "close": [600.0]})
    d = roll.implied_financing(sp, rates, divs, px, "ES")
    q = 6 / 600.0
    r_impl = q + (d["far_close"] / d["near_close"] - 1) * 365 / d["days_between"]
    assert np.allclose(d["implied_r"], r_impl) and np.allclose(d["richness_bp"], (r_impl - 0.04) * 1e4)
    ev = roll.roll_events(d, "ES")
    assert len(ev) == 1 and ev["roll_date"].iloc[0] == dt.date(2026, 9, 11) and abs(ev["spread_usd"].iloc[0] - 3000.0) < 1e-9


def test_eia_rolls_from_synthetic_history():
    days = calendars.business_days(dt.date(2023, 1, 2), dt.date(2023, 12, 29))
    eia = pd.DataFrame({"date": days, "root": "CL", "c1": 80.0, "c2": 80.5, "c3": 81.0, "c4": 81.5})
    ev = roll.eia_rolls(eia, "CL")
    assert 10 <= len(ev) <= 12 and np.allclose(ev["spread_usd"], 500.0) and ev["contango"].all()
    assert np.allclose(ev["roll_cost_long_usd"], 500.0 + 0.5 * 0.01 * 1000 + 2 * products.spec("CL")["fee_per_side"])


def _book():
    return {"start": "2026-09-01", "end": "2026-09-18", "cash_usd": 3e6, "positions": [{"root": "ES", "qty": 40, "role": "core", "pair": "equity"}, {"root": "NQ", "qty": -20, "role": "hedge", "pair": "equity"},
                                                                                       {"root": "ZN", "qty": 100, "role": "core", "pair": "curve"}, {"root": "ZB", "qty": -30, "role": "hedge", "pair": "curve"},
                                                                                       {"root": "CL", "qty": 25, "role": "core", "pair": "energy"}]}


REF = {"ES": (6000.0, 0.01), "NQ": (21000.0, 0.013), "ZN": (110.0, 0.003), "ZB": (115.0, 0.005), "CL": (70.0, 0.01)}


def test_clean_day_is_silent_and_every_fault_kind_is_caught():
    bk = _book(); date = dt.date(2026, 9, 10)
    pairs = {"equity": [("ES", 40), ("NQ", -20)], "curve": [("ZN", 100), ("ZB", -30)]}
    day = monitor.synthetic_day(date, bk, REF, None, seed=5)
    r = monitor.run_day(day, bk, date, bk["cash_usd"], pairs, [])
    assert r["n_alerts"] == 0
    caught = {}
    for seed in range(12):
        rng = np.random.default_rng(seed)
        day = monitor.synthetic_day(date, bk, REF, None, seed=100 + seed)
        faults = monitor.plant_faults(day, bk, date, rng, n_faults=4)
        r = monitor.run_day(day, bk, date, bk["cash_usd"], pairs, faults)
        for f in r["faults"]:
            caught.setdefault(f["kind"], []).append(f["caught"])
    assert set(caught) == set(monitor.FAULT_TYPES)
    for k, v in caught.items():
        assert any(v), k


def test_monitor_rules_directly():
    m = monitor.Monitor(dt.date(2026, 9, 10), 3e6, {}, positions={("ES", "ESZ26"): 10})
    m.on_mark(monitor.Mark(0.0, "ES", "ESZ26", 6000.0))
    m.on_fill(monitor.Fill(1.0, "f1", "ES", "ESZ26", 1, 2, 6000.0)); m.on_fill(monitor.Fill(2.0, "f1", "ES", "ESZ26", 1, 2, 6000.0))
    assert [a.rule for a in m.alerts] == ["DUPLICATE_FILL"] and m.net("ES") == 12
    m.on_fill(monitor.Fill(3.0, "f2", "ES", "ESZ26", 1, 200, 6000.0))
    assert {a.rule for a in m.alerts} >= {"FAT_FINGER", "POSITION_LIMIT"}
    m.on_mark(monitor.Mark(4.0, "ES", "ESZ26", 6100.0))
    assert "PRICE_BAND" in {a.rule for a in m.alerts}
    m.step(200.0)
    assert "STALE_MARK" in {a.rule for a in m.alerts}
    held = monitor.Monitor(dt.date(2026, 9, 14), 3e6, {}, positions={("ES", "ESU26"): 10}); held.on_mark(monitor.Mark(0.0, "ES", "ESU26", 6000.0)); held.step(20.0)
    assert "ROLL_NOT_DONE" in {a.rule for a in held.alerts}
    late = monitor.Monitor(dt.date(2026, 9, 22), 3e6, {}, positions={("ES", "ESU26"): 10}); late.on_mark(monitor.Mark(0.0, "ES", "ESU26", 6000.0)); late.step(20.0)
    assert "EXPIRY_RISK" in {a.rule for a in late.alerts}
