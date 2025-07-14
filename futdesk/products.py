"""Product specifications (configs/products.json): ticks, multipliers, contract rules, matching algorithm, fees, margins."""
from __future__ import annotations

import json
import os
from functools import lru_cache

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.environ.get("FUTDESK_PRODUCTS", os.path.join(ROOT, "configs", "products.json"))


@lru_cache(maxsize=None)
def config() -> dict:
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def spec(root: str) -> dict:
    s = dict(config()["products"][root])
    s["root"] = root
    s["tick_value"] = s["tick"] * s["multiplier"]
    return s


def roots() -> list[str]:
    return list(config()["products"])


def price_to_ticks(price: float, root: str) -> int:
    return int(round(price / spec(root)["tick"]))


def ticks_to_price(ticks: int, root: str) -> float:
    return ticks * spec(root)["tick"]


def matching_params(root: str) -> dict:
    """The allocation rule and its parameters as the matching engine wants them."""
    s = spec(root)
    algo = s.get("matching", "F")
    p = {"top": False, "top_min": 1, "top_max": 10**9, "fifo_pct": 0.0, "prorata_min": 1, "lmm_pct": 0.0}
    if algo == "F":
        p["fifo_pct"] = 1.0
    elif algo == "A":
        p.update({"top": True, "prorata_min": 2})
    elif algo == "Y":
        p.update({"top": True, "prorata_min": 2})
    elif algo == "C":
        p.update({"prorata_min": 1})
    p.update(s.get("matching_params", {}))
    p["algo"] = algo
    return p
