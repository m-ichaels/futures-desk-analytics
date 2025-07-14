"""CME business-day calendar and the contract-date rules (last trading day, first notice day, roll date) per product.

The holiday rules are written out (after the calendar module in execution-ops) so that they can be checked against the
trading days actually present in the settlement data; the contract rules are the ones in the CME/CBOT/NYMEX/COMEX
rulebooks, parameterised by name in configs/products.json."""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

ONE_DAY = dt.timedelta(days=1)
MONTH_CODES = "FGHJKMNQUVXZ"


def easter(year: int) -> dt.date:
    a = year % 19; b, c = divmod(year, 100); d, e = divmod(b, 4); f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30; i, k = divmod(c, 4); l = (32 + 2 * e + 2 * i - h - k) % 7; m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """n-th (1-based) weekday (Mon=0) of the month; n=-1 for the last one."""
    if n > 0:
        d = dt.date(year, month, 1)
        d += dt.timedelta(days=(weekday - d.weekday()) % 7)
        return d + dt.timedelta(days=7 * (n - 1))
    d = dt.date(year + (month == 12), month % 12 + 1, 1) - ONE_DAY
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def observed_us(d: dt.date) -> dt.date:
    if d.weekday() == 5:
        return d - ONE_DAY
    if d.weekday() == 6:
        return d + ONE_DAY
    return d


@lru_cache(maxsize=None)
def cme_holidays(year: int) -> frozenset[dt.date]:
    """Days without a CME settlement: the US exchange holiday set (Globex trades a shortened session on some of them,
    but the business-day calendar the contract rules use excludes them)."""
    h = set()
    ny = dt.date(year, 1, 1)
    if ny.weekday() == 6:
        h.add(ny + ONE_DAY)
    elif ny.weekday() < 5:
        h.add(ny)
    h.add(nth_weekday(year, 1, 0, 3))                 # Martin Luther King
    h.add(nth_weekday(year, 2, 0, 3))                 # Presidents' Day
    h.add(easter(year) - dt.timedelta(days=2))        # Good Friday
    h.add(nth_weekday(year, 5, 0, -1))                # Memorial Day
    if year >= 2022:
        h.add(observed_us(dt.date(year, 6, 19)))      # Juneteenth
    h.add(observed_us(dt.date(year, 7, 4)))
    h.add(nth_weekday(year, 9, 0, 1))                 # Labor Day
    h.add(nth_weekday(year, 11, 3, 4))                # Thanksgiving
    h.add(observed_us(dt.date(year, 12, 25)))
    special = {2001: ["09-11", "09-12", "09-13", "09-14"], 2004: ["06-11"], 2007: ["01-02"], 2012: ["10-29", "10-30"], 2018: ["12-05"], 2025: ["01-09"]}
    for md in special.get(year, []):
        h.add(dt.date.fromisoformat(f"{year}-{md}"))
    return frozenset(h)


def is_business_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in cme_holidays(d.year)


def add_business_days(d: dt.date, n: int) -> dt.date:
    step = ONE_DAY if n > 0 else -ONE_DAY
    for _ in range(abs(n)):
        d += step
        while not is_business_day(d):
            d += step
    return d


def prev_business_day(d: dt.date) -> dt.date:
    """d itself when it is a business day, otherwise the preceding one."""
    while not is_business_day(d):
        d -= ONE_DAY
    return d


def business_days(start: dt.date, end: dt.date) -> list[dt.date]:
    out, d = [], start
    while d <= end:
        if is_business_day(d):
            out.append(d)
        d += ONE_DAY
    return out


def last_business_day(year: int, month: int) -> dt.date:
    d = dt.date(year + (month == 12), month % 12 + 1, 1) - ONE_DAY
    return prev_business_day(d)


def first_business_day(year: int, month: int) -> dt.date:
    d = dt.date(year, month, 1)
    while not is_business_day(d):
        d += ONE_DAY
    return d


def _prior_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


# ---- contract rules --------------------------------------------------------------------------------------------------
def last_trading_day(rule: str, year: int, month: int) -> dt.date:
    if rule == "third_friday":                           # ES, NQ: the third Friday of the contract month
        return prev_business_day(nth_weekday(year, month, 4, 3))
    if rule == "cbot_treasury":                          # ZT, ZF, ZN, ZB: 7th business day preceding the last business day of the delivery month
        return add_business_days(last_business_day(year, month), -7)
    if rule == "imm_wednesday_minus_1":                  # SR3: the business day before the third Wednesday
        return add_business_days(nth_weekday(year, month, 2, 3), -1)
    if rule == "last_bd_of_month":                       # ZQ
        return last_business_day(year, month)
    if rule == "nymex_cl":                               # CL: 3 business days before the 25th of the month preceding the contract month
        py, pm = _prior_month(year, month)
        d25 = dt.date(py, pm, 25)
        anchor = d25 if is_business_day(d25) else prev_business_day(d25 - ONE_DAY)
        return add_business_days(anchor, -3)
    if rule == "nymex_ng":                               # NG: 3 business days before the first calendar day of the delivery month
        return add_business_days(dt.date(year, month, 1), -3)
    if rule == "comex_metals":                           # GC, SI: third-last business day of the delivery month
        return add_business_days(last_business_day(year, month), -2)
    raise ValueError(rule)


def first_notice_day(rule: str | None, year: int, month: int, ltd: dt.date) -> dt.date | None:
    if rule is None:
        return None
    if rule == "last_bd_prior_month":                    # CBOT treasuries, COMEX metals
        return last_business_day(*_prior_month(year, month))
    if rule == "bd_after_ltd":                           # NYMEX energy
        return add_business_days(ltd, 1)
    raise ValueError(rule)


def contract_code(root: str, year: int, month: int, digits: int = 2) -> str:
    y = year % 100 if digits == 2 else year % 10
    return f"{root}{MONTH_CODES[month - 1]}{y:0{digits}d}"


def parse_code(code: str, roots: list[str] | None = None) -> tuple[str, int, int]:
    """'ESZ5' or 'ESZ25' -> ('ES', 2025, 12).  One-digit years resolve to the decade nearest today."""
    for i in range(len(code) - 1, 0, -1):
        if code[i] in MONTH_CODES and code[i + 1:].isdigit():
            root, m, y = code[:i], MONTH_CODES.index(code[i]) + 1, code[i + 1:]
            if roots is not None and root not in roots:
                continue
            if len(y) == 1:
                base = dt.date.today().year
                cands = [base // 10 * 10 + int(y) + k for k in (-10, 0, 10)]
                year = min(cands, key=lambda c: abs(c - base))
            else:
                year = 2000 + int(y)
            return root, year, m
    raise ValueError(code)


def contract_dates(spec: dict, year: int, month: int) -> dict:
    ltd = last_trading_day(spec["ltd_rule"], year, month)
    fnd = first_notice_day(spec.get("fnd_rule"), year, month, ltd)
    # the roll date: cash-settled contracts roll the Thursday-or-so eight days before expiry (the equity convention of
    # rolling the week before); physically delivered contracts roll two business days before the earlier of first
    # notice and last trading day, so that no long is exposed to a delivery notice.
    if fnd is None:
        roll = add_business_days(ltd, -5)
    else:
        roll = add_business_days(min(fnd, ltd), -2)
    return {"year": year, "month": month, "ltd": ltd, "fnd": fnd, "roll": roll, "expiry_ym": f"{year}-{month:02d}"}


def listed_months(spec: dict, start: dt.date, end: dt.date) -> list[dict]:
    """Every contract month of the product whose last trading day falls in [start, end], in order."""
    out = []
    codes = spec["months"]
    for year in range(start.year - 1, end.year + 2):
        for c in codes:
            m = MONTH_CODES.index(c) + 1
            d = contract_dates(spec, year, m)
            if start <= d["ltd"] <= end:
                out.append(d)
    return sorted(out, key=lambda d: d["ltd"])


def front_month(spec: dict, on: dt.date, after_roll: bool = True) -> dict:
    """The contract a rolling long holds on the date: the nearest one whose roll date (or last trading day) is ahead."""
    for d in listed_months(spec, on, on + dt.timedelta(days=400)):
        key = d["roll"] if after_roll else d["ltd"]
        if key > on:
            return d
    raise ValueError("no contract")
