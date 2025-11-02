"""A CME Globex-style matching engine: price-time (FIFO), the Allocation algorithm (TOP order, then pro-rata with a
minimum allocation, then a FIFO leftover pass), the configurable split (a FIFO share before the pro-rata pool), and
implied orders between two outright books and their calendar spread (implied in and implied out, first generation
only, with implied liquidity behind the real orders at a price level).

Prices are integers in ticks.  Sides: +1 buy, -1 sell.  A calendar spread is quoted as leg1 - leg2 (the CME 'SP'
convention: buying the spread buys the first leg and sells the second)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class Order:
    id: int
    side: int
    price: int
    qty: int
    seq: int = 0
    top: bool = False
    filled: int = 0
    owner: str = "real"          # "implied" for the synthetic orders

    @property
    def remaining(self) -> int:
        return self.qty - self.filled


@dataclass
class Fill:
    taker_id: int
    maker_id: int
    price: int
    qty: int
    book: str = ""
    via: str = "outright"        # "implied" when the maker is an implied order


@dataclass
class Rule:
    """Allocation rule.  fifo_pct=1 -> pure FIFO.  top=True gives the order that improved the market first dibs
    (between top_min and top_max lots).  prorata_min is the minimum pro-rata allocation (lots below it are dropped
    into the leftover pass); lmm_pct is the share given to lead market makers before FIFO (not used here)."""
    algo: str = "F"
    fifo_pct: float = 1.0
    top: bool = False
    top_min: int = 1
    top_max: int = 10**9
    prorata_min: int = 1
    lmm_pct: float = 0.0

    @staticmethod
    def fifo() -> "Rule":
        return Rule("F", 1.0)

    @staticmethod
    def allocation(prorata_min: int = 2, top_max: int = 10**9) -> "Rule":
        return Rule("A", 0.0, True, 1, top_max, prorata_min)

    @staticmethod
    def split(fifo_pct: float = 0.4, prorata_min: int = 2, top: bool = True) -> "Rule":
        return Rule("Y", fifo_pct, top, 1, 10**9, prorata_min)


def allocate(orders: list[Order], qty: int, rule: Rule) -> list[tuple[Order, int]]:
    """Distribute an aggressor quantity over the resting orders of one price level (in time priority).  Returns
    (order, lots) pairs in the order they were matched.  Sum of lots = min(qty, level remaining)."""
    live = [o for o in orders if o.remaining > 0]
    total = sum(o.remaining for o in live)
    qty = min(qty, total)
    if qty <= 0:
        return []
    alloc = {o.id: 0 for o in live}
    left = qty
    # 1. TOP order
    if rule.top and live and live[0].top and live[0].remaining >= rule.top_min:
        a = min(left, live[0].remaining, rule.top_max)
        alloc[live[0].id] += a; left -= a
    # 2. FIFO share
    if left > 0 and rule.fifo_pct > 0:
        share = int(round(left * rule.fifo_pct)) if rule.fifo_pct < 1 else left
        for o in live:
            if share <= 0:
                break
            a = min(share, o.remaining - alloc[o.id])
            alloc[o.id] += a; share -= a; left -= a
    # 3. pro-rata with minimum allocation
    if left > 0 and rule.fifo_pct < 1:
        pool = [o for o in live if o.remaining - alloc[o.id] > 0]
        pool_total = sum(o.remaining - alloc[o.id] for o in pool)
        if pool_total > 0:
            V = left
            for o in pool:
                a = (V * (o.remaining - alloc[o.id])) // pool_total
                if a < rule.prorata_min:
                    a = 0
                a = min(a, o.remaining - alloc[o.id])
                alloc[o.id] += a; left -= a
    # 4. leftover FIFO
    if left > 0:
        for o in live:
            if left <= 0:
                break
            a = min(left, o.remaining - alloc[o.id])
            alloc[o.id] += a; left -= a
    return [(o, alloc[o.id]) for o in live if alloc[o.id] > 0]


class OrderBook:
    """One instrument.  Levels keep insertion order (FIFO); a price improvement gives the first order TOP status."""

    def __init__(self, name: str = "", rule: Rule | None = None):
        self.name = name
        self.rule = rule or Rule.fifo()
        self.bids: dict[int, list[Order]] = {}
        self.asks: dict[int, list[Order]] = {}
        self.orders: dict[int, Order] = {}
        self.seq = 0
        self.fills: list[Fill] = []
        self.implied: dict[int, list[Order]] = {1: [], -1: []}      # implied orders per side, refreshed by the SpreadComplex

    def side_levels(self, side: int) -> dict[int, list[Order]]:
        return self.bids if side == 1 else self.asks

    def best(self, side: int, with_implied: bool = True) -> tuple[int | None, int]:
        lv = self.side_levels(side)
        cands = [(p, sum(o.remaining for o in os_)) for p, os_ in lv.items() if any(o.remaining > 0 for o in os_)]
        if with_implied:
            for o in self.implied[side]:
                if o.remaining > 0:
                    cands.append((o.price, o.remaining))
        if not cands:
            return None, 0
        best_p = max(c[0] for c in cands) if side == 1 else min(c[0] for c in cands)
        return best_p, sum(q for p, q in cands if p == best_p)

    def depth(self, side: int, n: int = 5, with_implied: bool = True) -> list[tuple[int, int]]:
        lv = self.side_levels(side)
        agg: dict[int, int] = {}
        for p, os_ in lv.items():
            q = sum(o.remaining for o in os_)
            if q:
                agg[p] = agg.get(p, 0) + q
        if with_implied:
            for o in self.implied[side]:
                if o.remaining > 0:
                    agg[o.price] = agg.get(o.price, 0) + o.remaining
        ps = sorted(agg, reverse=(side == 1))[:n]
        return [(p, agg[p]) for p in ps]

    def _crosses(self, side: int, price: int, resting_price: int) -> bool:
        return price >= resting_price if side == 1 else price <= resting_price

    def _resting_prices(self, side: int) -> list[int]:
        """Opposite-side prices in matching order (best first), real and implied."""
        opp = -side
        ps = {p for p, os_ in self.side_levels(opp).items() if any(o.remaining > 0 for o in os_)}
        ps |= {o.price for o in self.implied[opp] if o.remaining > 0}
        return sorted(ps, reverse=(opp == 1))

    def submit(self, order: Order, on_implied_fill=None) -> list[Fill]:
        """Match against the opposite side (real orders at a level before implied ones), then rest the remainder."""
        fills: list[Fill] = []
        for p in self._resting_prices(order.side):
            if order.remaining <= 0 or not self._crosses(order.side, order.price, p):
                break
            real = self.side_levels(-order.side).get(p, [])
            for o, q in allocate(real, order.remaining, self.rule):
                o.filled += q; order.filled += q
                fills.append(Fill(order.id, o.id, p, q, self.name))
            if order.remaining > 0:
                for o in [x for x in self.implied[-order.side] if x.price == p and x.remaining > 0]:
                    q = min(order.remaining, o.remaining)
                    o.filled += q; order.filled += q
                    fills.append(Fill(order.id, o.id, p, q, self.name, "implied"))
                    if on_implied_fill:
                        on_implied_fill(self, o, q)
            self._purge(p, -order.side)
        if order.remaining > 0:
            self.seq += 1; order.seq = self.seq
            lv = self.side_levels(order.side)
            bp, _ = self.best(order.side, with_implied=False)
            order.top = bp is None or (order.price > bp if order.side == 1 else order.price < bp)
            if order.top:                                   # only one TOP order per side
                for os_ in lv.values():
                    for o in os_:
                        o.top = False
            lv.setdefault(order.price, []).append(order)
            self.orders[order.id] = order
        self.fills.extend(fills)
        return fills

    def cancel(self, order_id: int) -> bool:
        o = self.orders.pop(order_id, None)
        if o is None:
            return False
        lv = self.side_levels(o.side)
        lv[o.price] = [x for x in lv[o.price] if x.id != order_id]
        if not lv[o.price]:
            del lv[o.price]
        return True

    def modify(self, order_id: int, price: int | None = None, qty: int | None = None) -> bool:
        """A price change or a quantity increase loses time priority (and TOP); a quantity decrease keeps it."""
        o = self.orders.get(order_id)
        if o is None:
            return False
        new_p = o.price if price is None else price
        new_q = o.remaining if qty is None else qty
        if new_p == o.price and new_q <= o.remaining:
            o.qty = o.filled + new_q
            return True
        self.cancel(order_id)
        self.submit(Order(order_id, o.side, new_p, new_q))
        return True

    def _purge(self, price: int, side: int):
        lv = self.side_levels(side)
        if price in lv:
            lv[price] = [o for o in lv[price] if o.remaining > 0]
            for o in list(self.orders.values()):
                if o.remaining <= 0:
                    self.orders.pop(o.id, None)
            if not lv[price]:
                del lv[price]
        self.implied[side] = [o for o in self.implied[side] if o.remaining > 0]

    def resting(self) -> Iterator[Order]:
        for lv in (self.bids, self.asks):
            for os_ in lv.values():
                yield from os_


class SpreadComplex:
    """Two outright books and their calendar spread book (spread = leg1 - leg2), with first-generation implied orders.

    Implied out (in the spread book) from the outrights: bid = leg1 bid - leg2 ask, ask = leg1 ask - leg2 bid.
    Implied in (in the outrights) from the spread and the other leg: leg1 bid = spread bid + leg2 bid, leg1 ask =
    spread ask + leg2 ask; leg2 bid = leg1 bid - spread ask, leg2 ask = leg1 ask - spread bid.  Implied quantities are
    the minimum of the two components' displayed quantities; a fill against an implied order executes both components."""

    def __init__(self, rule: Rule | None = None, rule_spread: Rule | None = None):
        self.leg1 = OrderBook("leg1", rule); self.leg2 = OrderBook("leg2", rule); self.spread = OrderBook("spread", rule_spread or rule)
        self.next_implied_id = -1
        self.fills: list[Fill] = []
        self.refresh()

    def _real_best(self, b: OrderBook, side: int) -> tuple[int | None, int]:
        return b.best(side, with_implied=False)

    def _new(self, side: int, price: int, qty: int, src: tuple) -> Order:
        o = Order(self.next_implied_id, side, price, qty, owner="implied"); o.src = src   # type: ignore[attr-defined]
        self.next_implied_id -= 1
        return o

    def refresh(self):
        """Recompute every implied order from the real resting orders (first generation only)."""
        L1b, q1b = self._real_best(self.leg1, 1); L1a, q1a = self._real_best(self.leg1, -1)
        L2b, q2b = self._real_best(self.leg2, 1); L2a, q2a = self._real_best(self.leg2, -1)
        Sb, qsb = self._real_best(self.spread, 1); Sa, qsa = self._real_best(self.spread, -1)
        imp = {"spread": {1: [], -1: []}, "leg1": {1: [], -1: []}, "leg2": {1: [], -1: []}}
        if L1b is not None and L2a is not None:
            imp["spread"][1].append(self._new(1, L1b - L2a, min(q1b, q2a), ("leg1", 1, L1b, "leg2", -1, L2a)))
        if L1a is not None and L2b is not None:
            imp["spread"][-1].append(self._new(-1, L1a - L2b, min(q1a, q2b), ("leg1", -1, L1a, "leg2", 1, L2b)))
        if Sb is not None and L2b is not None:
            imp["leg1"][1].append(self._new(1, Sb + L2b, min(qsb, q2b), ("spread", 1, Sb, "leg2", 1, L2b)))
        if Sa is not None and L2a is not None:
            imp["leg1"][-1].append(self._new(-1, Sa + L2a, min(qsa, q2a), ("spread", -1, Sa, "leg2", -1, L2a)))
        if L1b is not None and Sa is not None:
            imp["leg2"][1].append(self._new(1, L1b - Sa, min(q1b, qsa), ("leg1", 1, L1b, "spread", -1, Sa)))
        if L1a is not None and Sb is not None:
            imp["leg2"][-1].append(self._new(-1, L1a - Sb, min(q1a, qsb), ("leg1", -1, L1a, "spread", 1, Sb)))
        for name, b in (("spread", self.spread), ("leg1", self.leg1), ("leg2", self.leg2)):
            b.implied = {1: [o for o in imp[name][1] if o.qty > 0], -1: [o for o in imp[name][-1] if o.qty > 0]}

    def _books(self) -> dict[str, OrderBook]:
        return {"leg1": self.leg1, "leg2": self.leg2, "spread": self.spread}

    def _on_implied_fill(self, book: OrderBook, imp: Order, qty: int):
        """Execute the two component real orders behind an implied order (FIFO at their levels)."""
        bn1, s1, p1, bn2, s2, p2 = imp.src   # type: ignore[attr-defined]
        for bn, s, p in ((bn1, s1, p1), (bn2, s2, p2)):
            b = self._books()[bn]
            level = b.side_levels(s).get(p, [])
            left = qty
            for o in level:
                if left <= 0:
                    break
                a = min(left, o.remaining); o.filled += a; left -= a
                f = Fill(imp.id, o.id, p, a, bn, "implied-leg"); b.fills.append(f); self.fills.append(f)
            b._purge(p, s)

    def submit(self, book: str, order: Order) -> list[Fill]:
        b = self._books()[book]
        fills = b.submit(order, self._on_implied_fill)
        self.fills.extend(fills)
        self.refresh()
        return fills

    def cancel(self, book: str, order_id: int) -> bool:
        ok = self._books()[book].cancel(order_id)
        self.refresh()
        return ok

    def quotes(self) -> dict:
        out = {}
        for name, b in self._books().items():
            out[name] = {"bid": b.best(1), "ask": b.best(-1), "bid_real": b.best(1, False), "ask_real": b.best(-1, False)}
        return out
