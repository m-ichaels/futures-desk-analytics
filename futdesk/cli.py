"""python -m futdesk run [--fast] [--tape FILE] | queue [--tape FILE] | roll | book | monitor [--days N] | match | sql "QUERY" | tapes"""
from __future__ import annotations

import json
import sys

from . import data


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__); return
    cmd, rest = argv[0], argv[1:]
    if cmd == "run":
        from . import run
        tapes = [rest[rest.index("--tape") + 1]] if "--tape" in rest else None
        r = run.run(fast="--fast" in rest, tapes=tapes)
        print(f"done in {r['seconds']:.0f} s -> {data.RESULTS}/run.json")
    elif cmd == "tapes":
        for t in data.tape_inventory():
            print(t)
    elif cmd == "roll":
        from . import roll
        out, _ = roll.summary(data.load_settlements(), data.load_eia_front(), data.load_rates(), data.load_dividends(), data.load_index_prices())
        print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "events"} for k, v in out.items()}, indent=1, default=str))
    elif cmd == "book":
        from . import pnl
        _, s = pnl.daily_book(pnl.load_book(), data.load_settlements(), data.load_rates())
        print(json.dumps(s, indent=1, default=str))
    elif cmd == "monitor":
        import datetime as dt
        from . import monitor, pnl
        bk = pnl.load_book(); n = int(rest[rest.index("--days") + 1]) if "--days" in rest else 10
        h = monitor.harness(bk, {p["root"]: (100.0, 0.01) for p in bk["positions"]}, None, dt.date.fromisoformat(bk["end"]), n_days=n)
        print(json.dumps({k: v for k, v in h.items() if k != "days"}, indent=1, default=str))
    elif cmd == "match":
        from .run import matching_examples
        print(json.dumps(matching_examples(), indent=1, default=str))
    elif cmd == "queue":
        from . import book, products, queue, calendars
        path = rest[rest.index("--tape") + 1] if "--tape" in rest else data.mbo_files()[0]
        df = data.load_mbo(path); sym = df["symbol"].iloc[0]; root = calendars.parse_code(sym, products.roots())[0]
        arr = data.mbo_arrays(df, products.spec(root)["tick"]); top = queue.top_series(arr); res = queue.run(arr, insert_every_s=5.0)
        s = queue.summarise(res, queue.fill_markouts(res, arr, top), n_boot=200)
        print(json.dumps({k: v for k, v in s.items() if k != "groups"}, indent=1)); print(json.dumps(s["groups"], indent=1))
    elif cmd == "sql":
        con = data.connect(read_only=True)
        print(con.execute(" ".join(rest)).df().to_string())
        con.close()
    else:
        print(__doc__)
