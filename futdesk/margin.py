"""SPAN-style margin for a futures book: scan risk per product (the price scan range per contract times the net
position), an intra-commodity charge for calendar spreads, inter-commodity credits for offsetting pairs.  The
parameters are in configs/products.json (the exchange's own SPAN files are the desk's input; these are the shape)."""
from __future__ import annotations

from . import products


def scan_risk(root: str, qty: int) -> float:
    return abs(qty) * products.spec(root)["price_scan_range"]


def book_margin(positions: dict[tuple[str, str], int]) -> dict:
    """positions: {(root, contract code): qty}.  Returns the margin and its components."""
    cfg = products.config()["span"]
    net: dict[str, int] = {}
    gross_by_root: dict[str, int] = {}
    for (root, _code), q in positions.items():
        net[root] = net.get(root, 0) + q
        gross_by_root[root] = gross_by_root.get(root, 0) + abs(q)
    scan = {r: scan_risk(r, q) for r, q in net.items()}
    # calendar spreads inside a product: the offset lots (gross - |net|) / 2 pay the spread charge instead of the outright
    calendar = {}
    for r in net:
        offset_lots = (gross_by_root[r] - abs(net[r])) // 2
        if offset_lots:
            calendar[r] = offset_lots * products.spec(r)["price_scan_range"] * cfg["calendar_spread_charge"].get(r, 0.25)
    # inter-commodity credits: opposite net positions in a listed pair, credit = rate x the smaller scan risk
    credits = {}
    remaining = dict(scan)
    for a, b, rate in cfg["intercommodity_credits"]:
        if a in net and b in net and net[a] * net[b] < 0:
            c = rate * min(remaining[a], remaining[b])
            credits[f"{a}/{b}"] = c
            used = min(remaining[a], remaining[b]); remaining[a] -= used; remaining[b] -= used   # the offset capacity is consumed once
    total = sum(scan.values()) + sum(calendar.values()) - sum(credits.values())
    return {"initial_margin": float(total), "scan": scan, "calendar_charge": calendar, "intercommodity_credit": credits, "net": net,
            "outright_sum": float(sum(scan.values()))}


def maintenance_ratio(root: str) -> float:
    s = products.spec(root)
    return s["maintenance_margin"] / s["initial_margin"]
