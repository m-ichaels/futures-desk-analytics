"""The positions-and-limits monitor for a futures book, and the fault-injection harness that scores it.

Rules (each alert names the rule, the product, the time and the detail): position limit per product, gross notional,
margin usage against the collateral pool and the maintenance call, a physically delivered contract held into its
first-notice window, a contract held past its last trading day, a roll not done after the roll date, a stale mark,
a fill or mark outside the price band around the last mark, a fill larger than the clip limit, a duplicate fill id,
a relative-value pair whose hedge ratio has broken, an intraday P&L drawdown, and the end-of-day reconciliation of
positions against the fills.

The harness builds simulated days on real price paths (the ES tape's mid for the index legs, settlement-calibrated
random walks for the others), plants faults from the catalogue on fault days, and scores: a fault is caught when an
alert of its rule for its product arrives within the detection window; clean days measure false alerts.  The days
are simulated - the README says so; the monitor's inputs on a desk are the drop copy and the price feed."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import calendars, margin, products

DEFAULT_LIMITS = {"position": {"ES": 100, "NQ": 60, "ZN": 250, "ZB": 100, "ZT": 300, "SR3": 500, "ZQ": 500, "CL": 60, "NG": 60, "GC": 30},
                  "gross_notional_usd": 120e6, "margin_usage": 0.60, "maintenance_usage": 0.85, "fnd_warning_bd": 1, "stale_s": 120.0,
                  "price_band_ticks": {"ES": 40, "NQ": 120, "ZN": 24, "ZB": 24, "ZT": 12, "SR3": 8, "ZQ": 8, "CL": 120, "NG": 120, "GC": 120},
                  "max_clip": {"ES": 50, "NQ": 30, "ZN": 120, "ZB": 50, "ZT": 100, "SR3": 200, "ZQ": 200, "CL": 30, "NG": 30, "GC": 15},
                  "pair_tolerance": 0.35, "drawdown_usd": 250000.0, "check_every_s": 15.0}

RULE_FOR_FAULT = {"fat_finger": ["FAT_FINGER", "POSITION_LIMIT"], "wrong_side": ["PAIR_BROKEN", "POSITION_JUMP"], "duplicate_fill": ["DUPLICATE_FILL"],
                  "bad_price": ["PRICE_BAND"], "stale_feed": ["STALE_MARK"], "missed_roll": ["ROLL_NOT_DONE", "DELIVERY_RISK", "EXPIRY_RISK"],
                  "margin_shock": ["MARGIN_USAGE", "MARGIN_CALL"], "hedge_dropped": ["PAIR_BROKEN"], "limit_breach": ["POSITION_LIMIT"],
                  "phantom_fill": ["RECON_BREAK", "POSITION_JUMP"], "drawdown": ["PNL_DRAWDOWN"]}
FAULT_TYPES = list(RULE_FOR_FAULT)


@dataclass
class Alert:
    t: float
    rule: str
    root: str
    detail: str


@dataclass
class Fill:
    t: float
    fill_id: str
    root: str
    code: str
    side: int
    qty: int
    price: float


@dataclass
class Mark:
    t: float
    root: str
    code: str
    price: float


class Monitor:
    def __init__(self, date: dt.date, cash_usd: float, pairs: dict[str, list[tuple[str, int]]], limits: dict | None = None, positions: dict | None = None):
        self.date = date; self.cash = cash_usd; self.pairs = pairs
        self.L = {**DEFAULT_LIMITS, **(limits or {})}
        self.pos: dict[tuple[str, str], int] = dict(positions or {})
        self.sod_pos = dict(self.pos)
        self.marks: dict[tuple[str, str], tuple[float, float]] = {}
        self.fills: list[Fill] = []; self.seen: set[str] = set()
        self.alerts: list[Alert] = []
        self.cash_flow = 0.0; self.pnl = 0.0; self.pnl_high = 0.0
        self.last_check = -1e9; self.cooldown: dict[tuple[str, str], float] = {}
        self.scan_mult = 1.0

    # -- helpers
    def alert(self, t: float, rule: str, root: str, detail: str, cooldown_s: float = 300.0):
        key = (rule, root)
        if t - self.cooldown.get(key, -1e9) < cooldown_s:
            return
        self.cooldown[key] = t
        self.alerts.append(Alert(t, rule, root, detail))

    def net(self, root: str) -> int:
        return sum(q for (r, _), q in self.pos.items() if r == root)

    def mark_value(self) -> float:
        v = 0.0
        for (r, c), q in self.pos.items():
            m = self.marks.get((r, c))
            if m:
                v += q * products.spec(r)["multiplier"] * m[1]
        return v

    def gross_notional(self) -> float:
        return sum(abs(q) * products.spec(r)["multiplier"] * self.marks[(r, c)][1] for (r, c), q in self.pos.items() if (r, c) in self.marks)

    # -- events
    def on_mark(self, m: Mark):
        last = self.marks.get((m.root, m.code))
        band = self.L["price_band_ticks"].get(m.root, 50) * products.spec(m.root)["tick"]
        if last and abs(m.price - last[1]) > band:
            self.alert(m.t, "PRICE_BAND", m.root, f"mark {m.price} vs last {last[1]} ({abs(m.price - last[1]) / products.spec(m.root)['tick']:.0f} ticks)")
        q = self.pos.get((m.root, m.code), 0)
        if last and q:
            self.pnl += q * products.spec(m.root)["multiplier"] * (m.price - last[1])
        self.marks[(m.root, m.code)] = (m.t, m.price)

    def on_fill(self, f: Fill):
        sp = products.spec(f.root)
        if f.fill_id in self.seen:
            self.alert(f.t, "DUPLICATE_FILL", f.root, f"fill id {f.fill_id} seen twice"); return
        self.seen.add(f.fill_id); self.fills.append(f)
        if f.qty > self.L["max_clip"].get(f.root, 100):
            self.alert(f.t, "FAT_FINGER", f.root, f"fill of {f.qty} lots against a clip limit of {self.L['max_clip'].get(f.root, 100)}")
        last = self.marks.get((f.root, f.code))
        band = self.L["price_band_ticks"].get(f.root, 50) * sp["tick"]
        if last and abs(f.price - last[1]) > band:
            self.alert(f.t, "PRICE_BAND", f.root, f"fill at {f.price} vs mark {last[1]}")
        before = self.net(f.root)
        self.pos[(f.root, f.code)] = self.pos.get((f.root, f.code), 0) + f.side * f.qty
        if last:
            self.pnl += f.side * f.qty * sp["multiplier"] * (last[1] - f.price)
        after = self.net(f.root)
        lim = self.L["position"].get(f.root, 100)
        if abs(after) > lim:
            self.alert(f.t, "POSITION_LIMIT", f.root, f"net {after} against a limit of {lim}")
        if before != 0 and np.sign(after) == -np.sign(before) and abs(after) > 0.5 * abs(before):
            self.alert(f.t, "POSITION_JUMP", f.root, f"net went from {before} to {after} on one fill")
        self._pair_check(f.t)

    def _pair_check(self, t: float):
        for name, legs in self.pairs.items():
            vals = []
            for root, target in legs:
                m = [v for (r, c), v in self.marks.items() if r == root]
                if not m:
                    vals = []; break
                vals.append((root, target, self.net(root), products.spec(root)["multiplier"] * m[-1][1]))
            if len(vals) < 2:
                continue
            (r1, t1, n1, v1), (r2, t2, n2, v2) = vals[0], vals[1]
            target_ratio = abs(t2 * v2) / max(abs(t1 * v1), 1e-9); actual = abs(n2 * v2) / max(abs(n1 * v1), 1e-9) if n1 else float("inf")
            if n1 == 0 or n2 == 0 or abs(actual / target_ratio - 1) > self.L["pair_tolerance"] or np.sign(n1) == np.sign(n2):
                self.alert(t, "PAIR_BROKEN", f"pair:{name}", f"hedge ratio {actual:.2f} against {target_ratio:.2f} ({r1} {n1}, {r2} {n2})", cooldown_s=900.0)

    def step(self, t: float):
        """Periodic checks: margin, notional, dates, stale marks, drawdown."""
        if t - self.last_check < self.L["check_every_s"]:
            return
        self.last_check = t
        m = margin.book_margin(self.pos); im = m["initial_margin"] * self.scan_mult
        usage = im / max(self.cash + self.pnl, 1.0)
        if usage > self.L["maintenance_usage"]:
            self.alert(t, "MARGIN_CALL", "BOOK", f"initial margin {im:,.0f} is {usage:.0%} of the collateral pool")
        elif usage > self.L["margin_usage"]:
            self.alert(t, "MARGIN_USAGE", "BOOK", f"margin usage {usage:.0%}")
        gn = self.gross_notional()
        if gn > self.L["gross_notional_usd"]:
            self.alert(t, "NOTIONAL_LIMIT", "BOOK", f"gross notional {gn / 1e6:.0f}m")
        for (r, c), q in self.pos.items():
            if q == 0:
                continue
            sp = products.spec(r); _, y, mth = calendars.parse_code(c, products.roots()); d = calendars.contract_dates(sp, y, mth)
            if d["fnd"] is not None and self.date >= calendars.add_business_days(d["fnd"], -self.L["fnd_warning_bd"]):
                self.alert(t, "DELIVERY_RISK", r, f"{c} held {q} with first notice {d['fnd']}", cooldown_s=3600)
            if self.date > d["ltd"]:
                self.alert(t, "EXPIRY_RISK", r, f"{c} held past its last trading day {d['ltd']}", cooldown_s=3600)
            elif self.date > d["roll"]:
                self.alert(t, "ROLL_NOT_DONE", r, f"{c} still held after the roll date {d['roll']}", cooldown_s=3600)
        for (r, c), q in self.pos.items():
            if q and (r, c) in self.marks and t - self.marks[(r, c)][0] > self.L["stale_s"]:
                self.alert(t, "STALE_MARK", r, f"no mark on {c} for {t - self.marks[(r, c)][0]:.0f} s", cooldown_s=self.L["stale_s"])
        self.pnl_high = max(self.pnl_high, self.pnl)
        if self.pnl_high - self.pnl > self.L["drawdown_usd"]:
            self.alert(t, "PNL_DRAWDOWN", "BOOK", f"drawdown {self.pnl_high - self.pnl:,.0f} from the day's high", cooldown_s=1800)

    def end_of_day(self, dropcopy: list[Fill], t: float) -> list[Alert]:
        """Positions from the start-of-day book plus the drop copy against what the monitor holds."""
        expected = dict(self.sod_pos)
        for f in dropcopy:
            expected[(f.root, f.code)] = expected.get((f.root, f.code), 0) + f.side * f.qty
        breaks = []
        for k in set(expected) | set(self.pos):
            if expected.get(k, 0) != self.pos.get(k, 0):
                breaks.append(k)
                self.alert(t, "RECON_BREAK", k[0], f"{k[1]}: monitor {self.pos.get(k, 0)} vs drop copy {expected.get(k, 0)}", cooldown_s=0)
        return [a for a in self.alerts if a.rule == "RECON_BREAK"]


# ---- the simulated day and the faults --------------------------------------------------------------------------------------
@dataclass
class Fault:
    kind: str
    t: float
    root: str
    detail: dict = field(default_factory=dict)


def synthetic_day(date: dt.date, book: dict, settle_ref: dict, es_mid_path: np.ndarray | None, seed: int, session_s: float = 6.5 * 3600, step_s: float = 5.0) -> dict:
    """Marks every 5 s for each held contract (index legs follow the recorded ES mid path rescaled, the others a random
    walk calibrated to the product's daily vol), the book's start-of-day positions, and the day's ordinary fills."""
    rng = np.random.default_rng(seed)
    n = int(session_s / step_s); t = np.arange(n) * step_s
    marks = []; sod = {}; fills = []
    for p in book["positions"]:
        root = p["root"]; sp = products.spec(root); hc = calendars.contract_dates(sp, *_held_ym(sp, date)); code = calendars.contract_code(root, hc["year"], hc["month"])
        s0, vol = settle_ref.get(root, (100.0, 0.01))
        if sp["asset_class"] == "equity" and es_mid_path is not None and len(es_mid_path) > 10:
            k = np.linspace(0, len(es_mid_path) - 1, n).astype(int); rel = es_mid_path[k] / es_mid_path[0]
            path = s0 * rel ** (vol / 0.012 if vol else 1.0)
        else:
            path = s0 * np.exp(np.cumsum(rng.normal(0, vol / np.sqrt(n), n)))
        path = np.round(path / sp["tick"]) * sp["tick"]
        sod[(root, code)] = p["qty"]
        for i in range(n):
            marks.append(Mark(float(t[i]), root, code, float(path[i])))
        # ordinary activity: a few small fills that net to zero-ish
        for j in range(rng.integers(2, 6)):
            ti = float(rng.uniform(600, session_s - 600)); side = int(rng.choice([-1, 1])); q = int(rng.integers(1, max(2, abs(p["qty"]) // 10 + 1)))
            px = float(path[min(n - 1, int(ti / step_s))])
            fills.append(Fill(ti, f"{root}-{j}-{seed}", root, code, side, q, px))
            t2 = min(ti + float(rng.uniform(60, 900)), session_s - 60)
            fills.append(Fill(t2, f"{root}-{j}b-{seed}", root, code, -side, q, float(path[min(n - 1, int(t2 / step_s))])))
    return {"marks": marks, "sod": sod, "fills": fills, "session_s": session_s}


def _held_ym(spec: dict, date: dt.date) -> tuple[int, int]:
    for d in calendars.listed_months(spec, date - dt.timedelta(days=5), date + dt.timedelta(days=400)):
        if d["roll"] >= date:
            return d["year"], d["month"]
    raise ValueError


def plant_faults(day: dict, book: dict, date: dt.date, rng: np.random.Generator, n_faults: int = 6) -> list[Fault]:
    """Choose faults from the catalogue and modify the day's events accordingly."""
    faults = []
    kinds = list(rng.choice(FAULT_TYPES, size=n_faults, replace=False))
    roots = [p["root"] for p in book["positions"]]
    fills = day["fills"]; marks = day["marks"]; S = day["session_s"]
    for kind in kinds:
        root = str(rng.choice(roots)); sp = products.spec(root)
        code = next(c for (r, c) in day["sod"] if r == root); qty0 = day["sod"][(root, code)]
        t = float(rng.uniform(900, S - 1800))
        px = next(m.price for m in reversed(marks) if m.root == root and m.t <= t)
        if kind == "fat_finger":
            q = DEFAULT_LIMITS["max_clip"].get(root, 50) * 3
            fills.append(Fill(t, f"fault-ff-{root}", root, code, int(np.sign(qty0) or 1), q, px)); fills.append(Fill(t + 30, f"fault-ff-{root}-u", root, code, -int(np.sign(qty0) or 1), q, px))
        elif kind == "wrong_side":
            fills.append(Fill(t, f"fault-ws-{root}", root, code, -int(np.sign(qty0) or 1), int(abs(qty0) * 1.6) + 1, px))
        elif kind == "duplicate_fill":
            f0 = Fill(t, f"fault-dup-{root}", root, code, 1, 2, px); fills.append(f0); fills.append(Fill(t + 5, f0.fill_id, root, code, 1, 2, px))
        elif kind == "bad_price":
            fills.append(Fill(t, f"fault-bp-{root}", root, code, 1, 1, px * 1.03))
        elif kind == "stale_feed":
            day["marks"] = [m for m in marks if not (m.root == root and t <= m.t < t + 600)]; marks = day["marks"]
        elif kind == "missed_roll":
            # the position sits in a contract whose roll date has passed (and first notice is near)
            prev = [d for d in calendars.listed_months(sp, date - dt.timedelta(days=200), date + dt.timedelta(days=5)) if d["roll"] < date]
            if prev:
                old = calendars.contract_code(root, prev[-1]["year"], prev[-1]["month"])
                day["sod"][(root, old)] = qty0; day["sod"][(root, code)] = 0
                day["marks"] += [Mark(m.t, root, old, m.price) for m in marks if m.root == root]
                t = 0.0
            else:
                continue
        elif kind == "margin_shock":
            day["scan_mult"] = (t, 2.0)
        elif kind == "hedge_dropped":
            hedge = [p for p in book["positions"] if p.get("role") == "hedge"]
            if not hedge:
                continue
            hp = hedge[int(rng.integers(len(hedge)))]; root = hp["root"]; code = next(c for (r, c) in day["sod"] if r == root)
            px = next(m.price for m in reversed(marks) if m.root == root and m.t <= t)
            fills.append(Fill(t, f"fault-hd-{root}", root, code, -int(np.sign(hp["qty"])), abs(hp["qty"]), px))
        elif kind == "limit_breach":
            lim = DEFAULT_LIMITS["position"].get(root, 100); q = lim - abs(qty0) + 5
            fills.append(Fill(t, f"fault-lb-{root}", root, code, int(np.sign(qty0) or 1), q, px))
        elif kind == "phantom_fill":
            day["phantom"] = Fill(t, f"fault-ph-{root}", root, code, 1, 3, px)
        elif kind == "drawdown":
            k0 = next(i for i, m in enumerate(marks) if m.root == root and m.t >= t)
            shock = -np.sign(qty0) * min(0.08, 1.5 * DEFAULT_LIMITS["drawdown_usd"] / max(abs(qty0) * sp["multiplier"] * px, 1.0))   # sized to breach the drawdown limit
            for m in marks[k0:]:
                if m.root == root:
                    m.price = round(m.price * (1 + shock) / sp["tick"]) * sp["tick"]
        faults.append(Fault(kind, t, root))
    day["fills"] = sorted(fills, key=lambda f: f.t)
    return faults


def run_day(day: dict, book: dict, date: dt.date, cash_usd: float, pairs: dict, faults: list[Fault] | None = None, detect_window_s: float = 900.0) -> dict:
    mon = Monitor(date, cash_usd, pairs, positions=day["sod"])
    events = [(m.t, 0, m) for m in day["marks"]] + [(f.t, 1, f) for f in day["fills"]]
    if day.get("phantom"):
        events.append((day["phantom"].t, 1, day["phantom"]))
    events.sort(key=lambda e: (e[0], e[1]))
    shock = day.get("scan_mult")
    for t, kind, ev in events:
        if shock and t >= shock[0]:
            mon.scan_mult = shock[1]
        if kind == 0:
            mon.on_mark(ev)
        else:
            mon.on_fill(ev)
        mon.step(t)
    mon.end_of_day(day["fills"], day["session_s"])
    alerts = mon.alerts
    scored = []
    pair_of = {root: f"pair:{name}" for name, legs in pairs.items() for root, _ in legs}
    for f in faults or []:
        rules = RULE_FOR_FAULT[f.kind]
        cand = [a for a in alerts if a.rule in rules and (a.root == f.root or a.root == "BOOK" or a.root == pair_of.get(f.root) or f.kind in ("margin_shock", "drawdown")) and f.t <= a.t <= f.t + detect_window_s]
        if f.kind in ("missed_roll", "phantom_fill"):
            cand = [a for a in alerts if a.rule in rules and a.root == f.root]
        first = min((a.t for a in cand), default=None)
        scored.append({"kind": f.kind, "root": f.root, "t": f.t, "caught": first is not None, "time_to_detect_s": (first - f.t) if first is not None else None,
                       "rule": next((a.rule for a in cand if a.t == first), None)})
    return {"alerts": [a.__dict__ for a in alerts], "faults": scored, "n_alerts": len(alerts)}


def harness(book: dict, settle_ref: dict, es_mid_path: np.ndarray | None, start: dt.date, n_days: int = 40, seed: int = 0) -> dict:
    """Alternate fault and clean days over n_days business days; score detection and false alerts."""
    rng = np.random.default_rng(seed)
    pairs: dict[str, list[tuple[str, int]]] = {}
    for p in book["positions"]:
        pairs.setdefault(p.get("pair", p["root"]), []).append((p["root"], p["qty"]))
    pairs = {k: v for k, v in pairs.items() if len(v) == 2}
    days = []; d = start
    for i in range(n_days):
        d = calendars.add_business_days(d, 1)
        day = synthetic_day(d, book, settle_ref, es_mid_path, seed=seed * 1000 + i)
        faults = plant_faults(day, book, d, rng) if i % 2 == 0 else []
        r = run_day(day, book, d, book.get("cash_usd", 1e7), pairs, faults)
        days.append({"date": str(d), "fault_day": i % 2 == 0, **r})
    fd = [x for x in days if x["fault_day"]]; cd = [x for x in days if not x["fault_day"]]
    allf = [f for x in fd for f in x["faults"]]
    by_kind = {}
    for k in FAULT_TYPES:
        fk = [f for f in allf if f["kind"] == k]
        ttd = [f["time_to_detect_s"] for f in fk if f["caught"]]
        by_kind[k] = {"injected": len(fk), "caught": sum(f["caught"] for f in fk), "median_ttd_s": float(np.median(ttd)) if ttd else None, "rules": sorted({f["rule"] for f in fk if f["rule"]})}
    caught = sum(f["caught"] for f in allf)
    return {"fault_days": len(fd), "clean_days": len(cd), "injected": len(allf), "caught": caught, "detection_rate": caught / max(1, len(allf)),
            "median_ttd_s": float(np.median([f["time_to_detect_s"] for f in allf if f["caught"]])) if caught else None,
            "false_alerts_per_clean_day": float(np.mean([x["n_alerts"] for x in cd])) if cd else None,
            "clean_day_alerts_by_rule": pd.Series([a["rule"] for x in cd for a in x["alerts"]]).value_counts().to_dict() if cd else {},
            "alerts_per_fault_day": float(np.mean([x["n_alerts"] for x in fd])) if fd else None, "by_kind": by_kind, "days": days}
