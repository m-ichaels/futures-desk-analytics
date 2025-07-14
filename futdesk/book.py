"""The order book rebuilt order by order from MDP3 MBO events, and the shadow orders that measure queue position on it.

Python reference implementation; futdesk._replay (cpp/replay.cpp) is the same algorithm compiled, used when built, and
tests/test_book.py checks that the two agree event for event.  Actions: A add, C cancel (delete), M modify (a price
change or a size increase loses priority, a size decrease keeps it), F fill of a resting order, T trade summary
(aggressor side and size; no book change), R clear.  Prices are integers in ticks."""
from __future__ import annotations

from bisect import bisect_left, insort

import numpy as np

try:
    from . import _replay as _ext
    HAVE_EXT = True
except ImportError:                      # pragma: no cover
    _ext = None
    HAVE_EXT = False

A, C, M, F, T, R = 1, 2, 3, 4, 5, 6


class Book:
    """orders: id -> [side, price, size, prio]; levels[side][price] is an insertion-ordered dict id -> size (FIFO)."""

    def __init__(self):
        self.orders: dict[int, list] = {}
        self.levels = {1: {}, -1: {}}
        self.total = {1: {}, -1: {}}
        self.prices = {1: [], -1: []}       # sorted ascending; best bid = prices[1][-1], best ask = prices[-1][0]
        self.prio = 0

    # -- level bookkeeping
    def _add_to_level(self, oid, side, price, size):
        lv = self.levels[side].get(price)
        if lv is None:
            self.levels[side][price] = {oid: size}; self.total[side][price] = size; insort(self.prices[side], price)
        else:
            lv[oid] = size; self.total[side][price] += size

    def _remove_from_level(self, oid, side, price, size):
        lv = self.levels[side][price]
        del lv[oid]
        t = self.total[side][price] - size
        if not lv:
            del self.levels[side][price]; del self.total[side][price]
            p = self.prices[side]; del p[bisect_left(p, price)]
        else:
            self.total[side][price] = t

    # -- events
    def clear(self):
        self.__init__()

    def add(self, oid, side, price, size):
        self.prio += 1
        self.orders[oid] = [side, price, size, self.prio]
        self._add_to_level(oid, side, price, size)

    def cancel(self, oid):
        o = self.orders.pop(oid, None)
        if o is not None:
            self._remove_from_level(oid, o[0], o[1], o[2])

    def modify(self, oid, side, price, size):
        o = self.orders.get(oid)
        if o is None:
            self.add(oid, side, price, size); return
        if price == o[1] and size <= o[2]:              # size decrease keeps priority
            self.levels[side][price][oid] = size; self.total[side][price] += size - o[2]; o[2] = size
            return
        self._remove_from_level(oid, o[0], o[1], o[2])
        self.prio += 1
        o[0], o[1], o[2], o[3] = side, price, size, self.prio
        self._add_to_level(oid, side, price, size)

    def fill(self, oid, size):
        o = self.orders.get(oid)
        if o is None:
            return
        left = o[2] - size
        if left <= 0:
            self._remove_from_level(oid, o[0], o[1], o[2]); del self.orders[oid]
        else:
            self.levels[o[0]][o[1]][oid] = left; self.total[o[0]][o[1]] -= size; o[2] = left

    # -- views
    def best(self, side):
        p = self.prices[side]
        if not p:
            return None
        return p[-1] if side == 1 else p[0]

    def depth(self, side, n=10):
        p = self.prices[side]
        px = p[::-1][:n] if side == 1 else p[:n]
        return [(x, self.total[side][x], len(self.levels[side][x])) for x in px]

    def queue(self, side, price):
        """(order id, size) in priority order at the level."""
        lv = self.levels[side].get(price)
        return list(lv.items()) if lv else []


def apply_event(book: Book, action, side, price, size, oid):
    if action == A:
        book.add(oid, side, price, size)
    elif action == C:
        book.cancel(oid)
    elif action == M:
        book.modify(oid, side, price, size)
    elif action == F:
        book.fill(oid, size)
    elif action == R:
        book.clear()


def replay_top(arr: dict, levels: int = 10, at_flags_last: bool = True):
    """Top-of-book arrays after every event (or after every packet when at_flags_last): used by the validation
    against MBP-10 and by everything that needs a mid.  Returns dict of arrays: idx, bid_px/sz/ct[levels], ask_..."""
    if _ext is not None:
        return _ext.replay_top(arr["action"], arr["side"], arr["price"], arr["size"], arr["order_id"], arr["flags"], levels, at_flags_last)
    n = len(arr["action"]); act, sd, px, sz, oid, fl = arr["action"], arr["side"], arr["price"], arr["size"], arr["order_id"], arr["flags"]
    book = Book()
    idx = []; bp = []; bs = []; bc = []; ap = []; asz = []; ac = []
    for i in range(n):
        apply_event(book, act[i], sd[i], px[i], sz[i], oid[i])
        if at_flags_last and not (fl[i] & 128):
            continue
        idx.append(i)
        for side, P, S, Cn in ((1, bp, bs, bc), (-1, ap, asz, ac)):
            d = book.depth(side, levels)
            d += [(0, 0, 0)] * (levels - len(d))
            P.append([x[0] for x in d]); S.append([x[1] for x in d]); Cn.append([x[2] for x in d])
    return {"idx": np.array(idx, dtype=np.int64), "bid_px": np.array(bp, dtype=np.int64).reshape(-1, levels), "bid_sz": np.array(bs, dtype=np.int64).reshape(-1, levels),
            "bid_ct": np.array(bc, dtype=np.int64).reshape(-1, levels), "ask_px": np.array(ap, dtype=np.int64).reshape(-1, levels),
            "ask_sz": np.array(asz, dtype=np.int64).reshape(-1, levels), "ask_ct": np.array(ac, dtype=np.int64).reshape(-1, levels)}
