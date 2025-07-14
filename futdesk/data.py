"""Paths, the DuckDB store and the loaders: Databento MBO/MBP (CSV or DBN) -> parquet, settlements (Yahoo per contract,
EIA contracts 1-4), SOFR, dividends.  FUTDESK_DATA points at another data root (the CI sample), FUTDESK_RESULTS at
another results folder."""
from __future__ import annotations

import datetime as dt
import glob
import json
import os

import duckdb
import numpy as np
import pandas as pd

from . import calendars, products

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("FUTDESK_DATA", os.path.join(ROOT, "data"))
RESULTS = os.environ.get("FUTDESK_RESULTS", os.path.join(ROOT, "results"))
RAW = os.path.join(DATA, "raw")
REF = os.path.join(DATA, "reference") if os.path.isdir(os.path.join(DATA, "reference")) else os.path.join(ROOT, "data", "reference")
DER = os.path.join(DATA, "derived")
DB = os.path.join(DER, "futdesk.duckdb")

ACTION_CODE = {"A": 1, "C": 2, "M": 3, "F": 4, "T": 5, "R": 6, "N": 0}
SIDE_CODE = {"B": 1, "A": -1, "N": 0}
PRICE_SCALE = 1_000_000_000          # DBN fixed-point: 1e-9 price units


def ensure_dirs():
    for p in (RAW, REF, DER, RESULTS, os.path.join(RESULTS, "figures")):
        os.makedirs(p, exist_ok=True)


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    ensure_dirs()
    return duckdb.connect(DB, read_only=read_only)


# ---- MBO ------------------------------------------------------------------------------------------------------------
MBO_COLS = ["ts", "ts_recv", "action", "side", "price", "size", "order_id", "flags", "sequence", "symbol"]


def mbo_csv_to_parquet(csv_path: str, out_path: str, symbols: list[str] | None = None) -> int:
    """Databento MBO CSV -> parquet with integer prices (1e-9 units), coded action/side and ns timestamps."""
    con = duckdb.connect()
    where = f"WHERE symbol IN ({', '.join(repr(s) for s in symbols)})" if symbols else ""
    con.execute(f"""
        COPY (SELECT epoch_ns(ts_event)::BIGINT AS ts, epoch_ns(ts_recv)::BIGINT AS ts_recv,
                     CASE action WHEN 'A' THEN 1 WHEN 'C' THEN 2 WHEN 'M' THEN 3 WHEN 'F' THEN 4 WHEN 'T' THEN 5 WHEN 'R' THEN 6 ELSE 0 END::TINYINT AS action,
                     CASE side WHEN 'B' THEN 1 WHEN 'A' THEN -1 ELSE 0 END::TINYINT AS side,
                     COALESCE(round(price * {PRICE_SCALE}), 0)::BIGINT AS price, size::INTEGER AS size, order_id::BIGINT AS order_id,
                     flags::SMALLINT AS flags, sequence::BIGINT AS sequence, symbol
              FROM read_csv('{csv_path}', header=true, types={{'ts_event': 'TIMESTAMP', 'ts_recv': 'TIMESTAMP', 'price': 'DOUBLE', 'order_id': 'BIGINT', 'sequence': 'BIGINT'}}) {where}
              ORDER BY ts_recv, sequence)
        TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
    n = con.execute(f"SELECT count(*) FROM '{out_path}'").fetchone()[0]
    con.close()
    return n


def mbp_csv_to_parquet(csv_path: str, out_path: str, levels: int = 10, symbols: list[str] | None = None) -> int:
    """Databento MBP-10 CSV -> parquet keeping sequence, ts and the level arrays (prices in 1e-9 units)."""
    con = duckdb.connect()
    cols = ", ".join(f"COALESCE(round(bid_px_{k:02d} * {PRICE_SCALE}), 0)::BIGINT AS bid_px_{k:02d}, COALESCE(round(ask_px_{k:02d} * {PRICE_SCALE}), 0)::BIGINT AS ask_px_{k:02d}, "
                     f"bid_sz_{k:02d}::INTEGER AS bid_sz_{k:02d}, ask_sz_{k:02d}::INTEGER AS ask_sz_{k:02d}, bid_ct_{k:02d}::INTEGER AS bid_ct_{k:02d}, ask_ct_{k:02d}::INTEGER AS ask_ct_{k:02d}" for k in range(levels))
    where = f"WHERE symbol IN ({', '.join(repr(s) for s in symbols)})" if symbols else ""
    con.execute(f"""
        COPY (SELECT epoch_ns(ts_event)::BIGINT AS ts, epoch_ns(ts_recv)::BIGINT AS ts_recv, sequence::BIGINT AS sequence, flags::SMALLINT AS flags, symbol, {cols}
              FROM read_csv('{csv_path}', header=true, types={{'ts_event': 'TIMESTAMP', 'ts_recv': 'TIMESTAMP', 'sequence': 'BIGINT'}}) {where}
              ORDER BY ts_recv, sequence)
        TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
    n = con.execute(f"SELECT count(*) FROM '{out_path}'").fetchone()[0]
    con.close()
    return n


def dbn_to_parquet(dbn_path: str, out_path: str, schema: str = "mbo") -> int:
    """Databento DBN (.dbn / .dbn.zst, from the paid API) -> the same parquet layout; needs the databento package."""
    import databento as db  # noqa: optional dependency
    store = db.DBNStore.from_file(dbn_path)
    df = store.to_df(price_type="fixed", pretty_ts=False).reset_index()
    if schema == "mbo":
        out = pd.DataFrame({"ts": df["ts_event"].astype("int64"), "ts_recv": df["ts_recv"].astype("int64"),
                            "action": df["action"].map(ACTION_CODE).fillna(0).astype("int8"), "side": df["side"].map(SIDE_CODE).fillna(0).astype("int8"),
                            "price": df["price"].astype("int64"), "size": df["size"].astype("int32"), "order_id": df["order_id"].astype("int64"),
                            "flags": df["flags"].astype("int16"), "sequence": df["sequence"].astype("int64"), "symbol": df["symbol"].astype(str)})
        out = out[(out["action"] != 1) | (out["price"] != 9223372036854775807)]      # DBN's null price sentinel
        out.loc[out["price"] == 9223372036854775807, "price"] = 0
    else:
        keep = ["ts_event", "ts_recv", "sequence", "flags", "symbol"] + [c for c in df.columns if c[:6] in ("bid_px", "ask_px", "bid_sz", "ask_sz", "bid_ct", "ask_ct")]
        out = df[keep].rename(columns={"ts_event": "ts"})
        out["ts"] = out["ts"].astype("int64"); out["ts_recv"] = out["ts_recv"].astype("int64")
    out.sort_values(["ts_recv", "sequence"]).to_parquet(out_path, index=False, compression="zstd")
    return len(out)


def mbo_files() -> list[str]:
    return sorted(glob.glob(os.path.join(DER, "mbo_*.parquet")))


def load_mbo(path: str, symbol: str | None = None) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if symbol is not None:
        df = df[df["symbol"] == symbol]
    return df.reset_index(drop=True)


def mbo_arrays(df: pd.DataFrame, tick: float) -> dict:
    """numpy arrays for the replay: prices in ticks, actions and sides as int8."""
    tick_units = int(round(tick * PRICE_SCALE))
    return {"ts": df["ts"].to_numpy(np.int64), "action": df["action"].to_numpy(np.int8), "side": df["side"].to_numpy(np.int8),
            "price": (df["price"].to_numpy(np.int64) // tick_units).astype(np.int64), "size": df["size"].to_numpy(np.int32),
            "order_id": df["order_id"].to_numpy(np.int64), "flags": df["flags"].to_numpy(np.int16), "sequence": df["sequence"].to_numpy(np.int64)}


def tape_inventory() -> list[dict]:
    """What MBO tapes exist: symbol, root, session date, events, span."""
    out = []
    for p in mbo_files():
        con = duckdb.connect()
        rows = con.execute(f"SELECT symbol, count(*), min(ts), max(ts) FROM '{p}' WHERE action <> 6 AND flags & 32 = 0 GROUP BY symbol").fetchall()
        con.close()
        for sym, n, a, b in rows:
            root = calendars.parse_code(sym, products.roots())[0]
            out.append({"file": os.path.basename(p), "symbol": sym, "root": root, "events": int(n), "start": pd.Timestamp(a, unit="ns", tz="UTC").isoformat(),
                        "end": pd.Timestamp(b, unit="ns", tz="UTC").isoformat(), "hours": (b - a) / 3.6e12})
    return out


# ---- settlements ----------------------------------------------------------------------------------------------------
def load_settlements() -> pd.DataFrame:
    """Daily closes per listed contract from data/reference/settlements.parquet: date, root, code, year, month, close, volume."""
    p = os.path.join(REF, "settlements.parquet")
    if not os.path.exists(p):
        return pd.DataFrame(columns=["date", "root", "code", "year", "month", "close", "volume", "source"])
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def load_eia_front() -> pd.DataFrame:
    """EIA daily NYMEX contracts 1-4 (real settlement prices): date, root, c1..c4."""
    frames = []
    for root in products.roots():
        p = os.path.join(REF, f"eia_{root.lower()}_front.csv")
        if os.path.exists(p):
            d = pd.read_csv(p); d["root"] = root; d["date"] = pd.to_datetime(d["date"]).dt.date
            frames.append(d)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "root", "c1", "c2", "c3", "c4"])


def load_rates() -> pd.DataFrame:
    p = os.path.join(REF, "rates.csv")
    if not os.path.exists(p):
        return pd.DataFrame(columns=["date", "SOFR", "EFFR"])
    d = pd.read_csv(p); d["date"] = pd.to_datetime(d["date"]).dt.date
    return d


def load_dividends() -> pd.DataFrame:
    p = os.path.join(REF, "dividends.csv")
    if not os.path.exists(p):
        return pd.DataFrame(columns=["symbol", "date", "dividend", "close"])
    d = pd.read_csv(p); d["date"] = pd.to_datetime(d["date"]).dt.date
    return d


def load_index_prices() -> pd.DataFrame:
    p = os.path.join(REF, "index_prices.csv")
    if not os.path.exists(p):
        return pd.DataFrame(columns=["symbol", "date", "close"])
    d = pd.read_csv(p); d["date"] = pd.to_datetime(d["date"]).dt.date
    return d


def write_json(obj, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1, default=_json_default)


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.ndarray,)):
        return o.tolist()
    if isinstance(o, (dt.date, dt.datetime, pd.Timestamp)):
        return o.isoformat()
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(type(o))
