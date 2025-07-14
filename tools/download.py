#!/usr/bin/env python3
"""Free data for the futures desk stack.   python tools/download.py [--skip-databento] [--skip-yahoo] [--skip-eia] [--roots ES,NQ,...]

  Databento  the public CME Globex MDP 3.0 sample (no key): one Globex session of the front E-mini S&P 500 contract, MBO
             (order by order) and MBP-10 -> data/raw/databento/*.csv -> data/derived/mbo_*.parquet, book_*.mbp10.parquet
             With DATABENTO_API_KEY set: MBO for the products/days in configs/databento.json through the paid API (DBN)
  Yahoo      every listed contract of each product (daily closes, 2y range) and the continuous front (ES=F, ...), plus
             SPY/QQQ closes and dividends -> data/reference/settlements.parquet, index_prices.csv, dividends.csv
  EIA        daily NYMEX settlements for contracts 1-4 (WTI since 1983, Henry Hub since 1994) -> data/reference/eia_*_front.csv
  NY Fed     SOFR and EFFR -> data/reference/rates.csv

Yahoo keeps a contract only while it is listed (an expired contract disappears within weeks), which is why the
settlements file is committed: each run adds what is listed today to what was already saved."""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.request

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from futdesk import calendars, data, products  # noqa: E402

ARGS = sys.argv[1:]
RAW, REF, DER = data.RAW, data.REF, data.DER
UA = {"User-Agent": "Mozilla/5.0 (futures-desk-analytics; research)"}


def get(url, retries=3, timeout=60, headers=None):
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            print(f"    {url[:90]}: {e}")
            time.sleep(1.5 * (k + 1))
    return None


# ---- Databento --------------------------------------------------------------------------------------------------------
def databento_sample():
    out = os.path.join(RAW, "databento"); os.makedirs(out, exist_ok=True)
    for schema in ("mbo", "mbp-10"):
        path = os.path.join(out, f"glbx-mdp3-sample.{schema}.csv")
        if os.path.exists(path) and os.path.getsize(path) > 1e6:
            print(f"  {schema}: have {path}")
            continue
        url = f"https://hist.databento.com/v0/dataset/sample/download/glbx.mdp3/{schema}?sample_type=futures"
        print(f"  {schema}: downloading the public sample (large)")
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=600) as r, open(path, "wb") as f:
            while True:
                chunk = r.read(1 << 22)
                if not chunk:
                    break
                f.write(chunk)
        print(f"    {os.path.getsize(path) / 1e6:.0f} MB")
    mbo = os.path.join(DER, "mbo_glbx-mdp3-sample.parquet"); mbp = os.path.join(DER, "book_glbx-mdp3-sample.mbp10.parquet")
    if not os.path.exists(mbo):
        print("  mbo -> parquet:", data.mbo_csv_to_parquet(os.path.join(out, "glbx-mdp3-sample.mbo.csv"), mbo), "events")
    if not os.path.exists(mbp):
        print("  mbp-10 -> parquet:", data.mbp_csv_to_parquet(os.path.join(out, "glbx-mdp3-sample.mbp-10.csv"), mbp), "rows")


def databento_api():
    """MBO for the requested products and days through the paid API (DATABENTO_API_KEY); configs/databento.json lists them."""
    key = os.environ.get("DATABENTO_API_KEY")
    cfg_path = os.path.join(ROOT, "configs", "databento.json")
    if not key or not os.path.exists(cfg_path):
        print("  DATABENTO_API_KEY not set: the paid MBO days (rates, energy) are skipped; the public ES sample is used")
        return
    try:
        import databento as db
    except ImportError:
        print("  pip install databento to fetch the paid days"); return
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    client = db.Historical(key)
    out = os.path.join(RAW, "databento"); os.makedirs(out, exist_ok=True)
    for req in cfg["requests"]:
        for day in req["days"]:
            name = f"{req['symbol']}_{day}"
            path = os.path.join(out, f"{name}.mbo.dbn.zst"); der = os.path.join(DER, f"mbo_{name}.parquet")
            if os.path.exists(der):
                print(f"  {name}: have"); continue
            start = f"{day}T00:00"; end = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat() + "T00:00"
            cost = client.metadata.get_cost(dataset="GLBX.MDP3", symbols=[req["symbol"]], schema="mbo", start=start, end=end)
            print(f"  {name}: ${cost:.2f}")
            if cost > cfg.get("max_cost_per_request_usd", 10):
                print("    over the per-request cap in configs/databento.json; skipped"); continue
            client.timeseries.get_range(dataset="GLBX.MDP3", symbols=[req["symbol"]], schema="mbo", start=start, end=end, path=path)
            print("    ->", data.dbn_to_parquet(path, der, "mbo"), "events")


# ---- Yahoo --------------------------------------------------------------------------------------------------------------
def yahoo_chart(sym, rng, interval, events=None):
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval={interval}" + (f"&events={events}" if events else "")
    raw = get(url, retries=2, timeout=30)
    if raw is None:
        return None
    j = json.loads(raw)
    res = j.get("chart", {}).get("result")
    if not res:
        return None
    r = res[0]
    ts = r.get("timestamp", [])
    q = r["indicators"]["quote"][0]
    rows = [(t, q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]) for i, t in enumerate(ts) if q["close"][i] is not None]
    divs = r.get("events", {}).get("dividends", {})
    return rows, divs


def yahoo_contracts(roots):
    have = data.load_settlements()
    frames = [have] if len(have) else []
    listed = []
    today = dt.date.today()
    for root in roots:
        sp = products.spec(root)
        n_root = 0
        for year in range(today.year - 1, today.year + 4):
            for c in sp["months"]:
                m = calendars.MONTH_CODES.index(c) + 1
                ltd = calendars.last_trading_day(sp["ltd_rule"], year, m)
                if ltd < today - dt.timedelta(days=45) or ltd > today + dt.timedelta(days=3 * 365):
                    continue
                code = calendars.contract_code(root, year, m); sym = f"{code}.{sp['yahoo_suffix']}"
                got = yahoo_chart(sym, "2y", "1d")
                time.sleep(0.15)
                if not got or not got[0]:
                    continue
                rows = got[0]
                df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
                df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("America/New_York").dt.date
                df = df.drop_duplicates("date", keep="last")
                df["root"] = root; df["code"] = code; df["year"] = year; df["month"] = m; df["source"] = "yahoo"
                frames.append(df[["date", "root", "code", "year", "month", "close", "volume", "source"]])
                listed.append({"symbol": sym, "root": root, "n": len(df), "from": str(df["date"].min()), "to": str(df["date"].max())}); n_root += 1
        print(f"  {root}: {n_root} listed contracts with daily history")
    if frames:
        allf = pd.concat(frames, ignore_index=True)
        allf["date"] = pd.to_datetime(allf["date"]).dt.date
        allf = allf.sort_values(["root", "code", "date"]).drop_duplicates(["root", "code", "date"], keep="last")
        allf.to_parquet(os.path.join(REF, "settlements.parquet"), index=False)
        print(f"  settlements.parquet: {len(allf):,} rows, {allf['code'].nunique()} contracts")
    with open(os.path.join(REF, "yahoo_listed.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(listed, f, indent=1)


def yahoo_continuous(roots):
    rows_all = []
    for root in roots:
        got = yahoo_chart(f"{root}=F", "5y", "1d")
        if got and got[0]:
            for t, o, h, lo, c, v in got[0]:
                rows_all.append({"date": pd.Timestamp(t, unit="s", tz="UTC").tz_convert("America/New_York").date().isoformat(), "root": root, "close": c, "volume": v})
        time.sleep(0.2)
    pd.DataFrame(rows_all).to_csv(os.path.join(REF, "continuous_front.csv"), index=False)
    print(f"  continuous fronts: {len(rows_all):,} rows")


def yahoo_index():
    prices, divs = [], []
    for sym in ("SPY", "QQQ", "^GSPC", "^NDX"):
        got = yahoo_chart(sym, "5y", "1d", events="div")
        if not got:
            continue
        for t, o, h, lo, c, v in got[0]:
            prices.append({"symbol": sym, "date": pd.Timestamp(t, unit="s", tz="UTC").tz_convert("America/New_York").date().isoformat(), "close": c})
        for k, d in got[1].items():
            divs.append({"symbol": sym, "date": pd.Timestamp(int(d["date"]), unit="s", tz="UTC").tz_convert("America/New_York").date().isoformat(), "dividend": d["amount"]})
        time.sleep(0.2)
    pd.DataFrame(prices).to_csv(os.path.join(REF, "index_prices.csv"), index=False)
    pd.DataFrame(divs).sort_values(["symbol", "date"]).to_csv(os.path.join(REF, "dividends.csv"), index=False)
    print(f"  index prices {len(prices):,} rows, dividends {len(divs)} rows")


# ---- EIA --------------------------------------------------------------------------------------------------------------------
def eia_xls(url):
    import xlrd
    raw = get(url)
    if raw is None:
        return []
    wb = xlrd.open_workbook(file_contents=raw)
    sh = wb.sheet_by_name("Data 1")
    out = []
    for r in range(sh.nrows):
        c0 = sh.cell(r, 0)
        if c0.ctype != xlrd.XL_CELL_DATE:
            continue
        d = xlrd.xldate_as_datetime(c0.value, wb.datemode).date()
        v = sh.cell(r, 1).value
        if isinstance(v, (int, float)) and v != "":
            out.append((d.isoformat(), float(v)))
    return out


def eia(roots):
    for root in roots:
        sp = products.spec(root)
        if "eia_series" not in sp:
            continue
        series = {}
        for k, code in enumerate(sp["eia_series"], 1):
            rows = eia_xls(f"https://www.eia.gov/dnav/{sp['eia_folder']}/hist_xls/{code}.xls")
            print(f"  EIA {code}: {len(rows)} rows")
            for d, v in rows:
                series.setdefault(d, {})[f"c{k}"] = v
        path = os.path.join(REF, f"eia_{root.lower()}_front.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["date", "c1", "c2", "c3", "c4"])
            for d in sorted(series):
                r = series[d]; w.writerow([d] + [r.get(f"c{k}", "") for k in range(1, 5)])


# ---- NY Fed --------------------------------------------------------------------------------------------------------------
def nyfed():
    out = {}
    for name, url in (("SOFR", "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json?startDate=2018-04-01&endDate=2030-12-31"),
                      ("EFFR", "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json?startDate=2016-01-01&endDate=2030-12-31")):
        raw = get(url, timeout=120)
        if raw is None:
            continue
        for r in json.loads(raw)["refRates"]:
            out.setdefault(r["effectiveDate"], {})[name] = r["percentRate"]
    df = pd.DataFrame([{"date": d, **v} for d, v in sorted(out.items())])
    df.to_csv(os.path.join(REF, "rates.csv"), index=False)
    print(f"  rates: {len(df)} days")


def main():
    data.ensure_dirs()
    roots = [r for r in (ARGS[ARGS.index("--roots") + 1].split(",") if "--roots" in ARGS else products.roots())]
    if "--skip-databento" not in ARGS:
        print("Databento"); databento_sample(); databento_api()
    if "--skip-eia" not in ARGS:
        print("EIA"); eia(roots)
    print("NY Fed"); nyfed()
    if "--skip-yahoo" not in ARGS:
        print("Yahoo"); yahoo_contracts(roots); yahoo_continuous(roots); yahoo_index()
    print("done")


if __name__ == "__main__":
    main()
