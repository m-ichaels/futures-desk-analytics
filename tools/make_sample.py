#!/usr/bin/env python3
"""The committed CI sample: a window of the ES MBO tape with a synthetic snapshot at its start (the book rebuilt up
to that time and emitted as R + A rows flagged as a snapshot), and the MBP-10 rows of a shorter window for the
validation test.   python tools/make_sample.py [--from 13:30] [--minutes 15] [--mbp-minutes 3]"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from futdesk import book, data  # noqa: E402

args = sys.argv[1:]
start = args[args.index("--from") + 1] if "--from" in args else "13:30"
minutes = int(args[args.index("--minutes") + 1]) if "--minutes" in args else 15
mbp_minutes = int(args[args.index("--mbp-minutes") + 1]) if "--mbp-minutes" in args else 3

src = os.path.join(ROOT, "data", "derived", "mbo_glbx-mdp3-sample.parquet")
df = pd.read_parquet(src)
day = pd.Timestamp(int(df["ts"].iloc[-1]), unit="ns", tz="UTC").strftime("%Y-%m-%d")
t0 = int(pd.Timestamp(f"{day} {start}", tz="UTC").value); t1 = t0 + minutes * 60 * 10**9
before = df[df["ts"] < t0]
b = book.Book()
for r in before.itertuples(index=False):
    book.apply_event(b, r.action, r.side, r.price, r.size, r.order_id)
rows = [{"ts": t0, "ts_recv": t0, "action": 6, "side": 0, "price": 0, "size": 0, "order_id": 0, "flags": 40, "sequence": 0, "symbol": df["symbol"].iloc[0]}]
for oid, (side, price, size, prio) in sorted(b.orders.items(), key=lambda kv: kv[1][3]):
    rows.append({"ts": t0, "ts_recv": t0, "action": 1, "side": side, "price": price, "size": size, "order_id": oid, "flags": 40, "sequence": 0, "symbol": df["symbol"].iloc[0]})
rows[-1]["flags"] = 40 | 128
snap = pd.DataFrame(rows).astype(df.dtypes.to_dict())
win = df[(df["ts"] >= t0) & (df["ts"] < t1)]
out_dir = os.path.join(ROOT, "data", "sample", "derived"); os.makedirs(out_dir, exist_ok=True)
sample = pd.concat([snap, win], ignore_index=True)
sample.to_parquet(os.path.join(out_dir, "mbo_sample_es.parquet"), index=False, compression="zstd")
print("mbo sample:", len(sample), "rows (", len(snap), "snapshot +", len(win), "events),", os.path.getsize(os.path.join(out_dir, "mbo_sample_es.parquet")) / 1e6, "MB")
mbp = pd.read_parquet(src.replace("mbo_", "book_").replace(".parquet", ".mbp10.parquet"))
mbp = mbp[(mbp["ts"] >= t0) & (mbp["ts"] < t0 + mbp_minutes * 60 * 10**9)]
mbp.to_parquet(os.path.join(out_dir, "book_sample_es.mbp10.parquet"), index=False, compression="zstd")
print("mbp sample:", len(mbp), "rows,", os.path.getsize(os.path.join(out_dir, "book_sample_es.mbp10.parquet")) / 1e6, "MB")
