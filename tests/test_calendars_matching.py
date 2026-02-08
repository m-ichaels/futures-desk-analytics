"""Contract dates against the exchange's published ones; the matching rules against hand-worked allocations."""
import datetime as dt
import random

import pytest

from futdesk import calendars as cal
from futdesk import matching as M
from futdesk import products


@pytest.mark.parametrize("root,year,month,ltd,fnd", [
    ("ES", 2025, 12, dt.date(2025, 12, 19), None), ("ES", 2026, 9, dt.date(2026, 9, 18), None), ("NQ", 2026, 3, dt.date(2026, 3, 20), None),
    ("ZN", 2025, 12, dt.date(2025, 12, 19), dt.date(2025, 11, 28)), ("ZN", 2026, 9, dt.date(2026, 9, 21), dt.date(2026, 8, 31)),
    ("CL", 2025, 11, dt.date(2025, 10, 21), dt.date(2025, 10, 22)), ("CL", 2025, 12, dt.date(2025, 11, 20), dt.date(2025, 11, 21)),
    ("NG", 2025, 11, dt.date(2025, 10, 29), dt.date(2025, 10, 30)), ("GC", 2025, 12, dt.date(2025, 12, 29), dt.date(2025, 11, 28)),
    ("SR3", 2025, 12, dt.date(2025, 12, 16), None), ("ZQ", 2025, 12, dt.date(2025, 12, 31), None)])
def test_contract_dates(root, year, month, ltd, fnd):
    d = cal.contract_dates(products.spec(root), year, month)
    assert d["ltd"] == ltd and d["fnd"] == fnd
    assert d["roll"] < min(x for x in (d["ltd"], d["fnd"]) if x is not None)


def test_holidays_and_business_days():
    assert not cal.is_business_day(dt.date(2026, 4, 3))          # Good Friday
    assert not cal.is_business_day(dt.date(2026, 7, 3))          # Independence Day observed
    assert cal.is_business_day(dt.date(2026, 7, 6))
    assert cal.add_business_days(dt.date(2026, 7, 2), 1) == dt.date(2026, 7, 6)
    assert cal.last_business_day(2026, 5) == dt.date(2026, 5, 29)


def test_codes_round_trip():
    assert cal.contract_code("ES", 2025, 12) == "ESZ25" and cal.contract_code("ES", 2025, 12, 1) == "ESZ5"
    assert cal.parse_code("ESZ25") == ("ES", 2025, 12) and cal.parse_code("ESZ5", ["ES"]) == ("ES", 2025, 12)
    assert cal.parse_code("SR3H27", ["SR3", "ES"]) == ("SR3", 2027, 3)
    fm = cal.front_month(products.spec("ES"), dt.date(2026, 9, 14))
    assert (fm["year"], fm["month"]) == (2026, 12)                # after the September roll date (11 Sep) the long holds December


def level():
    return [M.Order(1, 1, 100, 100, top=True), M.Order(2, 1, 100, 50), M.Order(3, 1, 100, 30), M.Order(4, 1, 100, 1)]


def test_allocation_rules_known_answers():
    assert {o.id: q for o, q in M.allocate(level(), 60, M.Rule.fifo())} == {1: 60}
    assert {o.id: q for o, q in M.allocate(level(), 60, M.Rule("C", 0.0, False, 1, 10**9, 2))} == {1: 35, 2: 16, 3: 9}      # floors 33/16/9, 1-lot dropped, 2 leftover FIFO
    assert {o.id: q for o, q in M.allocate(level(), 60, M.Rule.allocation(2, top_max=10))} == {1: 38, 2: 14, 3: 8}          # TOP 10, then 50 pro-rata over 171, leftover 2
    assert {o.id: q for o, q in M.allocate(level(), 60, M.Rule.split(0.4, 2, False))} == {1: 43, 2: 11, 3: 6}                # 24 FIFO, 36 pro-rata over 157, leftover 2


def test_allocation_conservation_random():
    rng = random.Random(3)
    for _ in range(300):
        orders = [M.Order(i, 1, 100, rng.randint(1, 80), top=(i == 0)) for i in range(rng.randint(1, 8))]
        qty = rng.randint(1, 200)
        rule = rng.choice([M.Rule.fifo(), M.Rule.allocation(2), M.Rule.split(0.3, 2), M.Rule("C", 0.0, False, 1, 10**9, 1)])
        al = M.allocate(orders, qty, rule)
        assert sum(q for _, q in al) == min(qty, sum(o.qty for o in orders))
        assert all(0 < q <= o.qty for o, q in al)


def test_fifo_book_price_time_and_top():
    b = M.OrderBook("x", M.Rule.fifo())
    b.submit(M.Order(1, 1, 100, 5)); b.submit(M.Order(2, 1, 100, 3)); b.submit(M.Order(3, 1, 101, 2))
    assert not b.orders[1].top and not b.orders[2].top and b.orders[3].top     # TOP moved to the price improver
    f = b.submit(M.Order(9, -1, 100, 6))
    assert [(x.maker_id, x.price, x.qty) for x in f] == [(3, 101, 2), (1, 100, 4)]
    assert b.best(1) == (100, 4)
    assert b.modify(1, qty=1) and b.orders[1].remaining == 1 and b.orders[1].seq < b.orders[2].seq   # size decrease keeps priority
    assert b.modify(1, qty=4) and b.orders[1].seq > b.orders[2].seq                                  # increase loses it


def test_implied_in_and_out():
    c = M.SpreadComplex()
    c.submit("leg1", M.Order(1, 1, 1000, 10)); c.submit("leg1", M.Order(2, -1, 1002, 10))
    c.submit("leg2", M.Order(3, 1, 990, 4)); c.submit("leg2", M.Order(4, -1, 993, 6))
    q = c.quotes()
    assert q["spread"]["bid"] == (7, 6) and q["spread"]["ask"] == (12, 4)          # implied out: L1b - L2a, L1a - L2b
    c.submit("spread", M.Order(5, 1, 5, 3))
    q = c.quotes()
    assert q["leg2"]["ask"] == (993, 6)                                              # implied leg2 ask = L1a - Sb = 997 is worse than the real 993
    assert [o.price for o in c.leg1.implied[1]] == [995]                             # implied leg1 bid = Sb + L2b
    f = c.submit("spread", M.Order(6, -1, 7, 2))
    assert len(f) == 1 and f[0].via == "implied" and f[0].price == 7 and f[0].qty == 2
    legs = [(x.book, x.maker_id, x.qty) for x in c.fills if x.via == "implied-leg"]
    assert sorted(legs) == [("leg1", 1, 2), ("leg2", 4, 2)]
    assert c.quotes()["leg1"]["bid"] == (1000, 8) and c.quotes()["leg2"]["ask"] == (993, 4)
