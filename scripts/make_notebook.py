#!/usr/bin/env python3
"""Writes notebooks/results.ipynb, a walkthrough of results/run.json, the derived tables, the DuckDB store and the
figures; with --execute the code cells run in-process (no Jupyter needed) and their outputs are stored.
python scripts/make_notebook.py [--execute]"""
import base64
import contextlib
import io
import json
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cells = []


def md(s):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": s})


def code(s):
    cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": s})


md("# futures-desk-analytics: results walkthrough\n\nLoads `results/run.json`, the derived tables under `data/derived/`, the DuckDB store and the figures produced by `scripts/run_all.sh` (or `python -m futdesk run`). `FUTDESK_DATA=data/sample` points everything at the committed CI sample.")
code("import json, os\nimport numpy as np, pandas as pd\nfrom IPython.display import Image, display\nos.chdir(os.path.dirname(os.getcwd()) if os.path.basename(os.getcwd()) == 'notebooks' else os.getcwd())\nrun = json.load(open('results/run.json', encoding='utf-8'))\nDER = os.path.join(os.environ.get('FUTDESK_DATA', 'data'), 'derived')\nfor t in run['tapes']:\n    print(f\"{t['symbol']}: {t['events']:,} MBO events, {t['start'][:16]} to {t['end'][11:16]} UTC; book vs MBP-10: {t.get('validation', {}).get('mismatches')} mismatches over {t.get('validation', {}).get('fields', 0):,} fields\")\nprint(f\"pipeline {run['seconds']:.0f} s, compiled replay {run['ext']}\")")
md("## 1. Queue-position value on the recorded tape\n\nShadow orders at the touch every second, at the front / middle / back of the queue, under FIFO (as the tape was matched) and under pro-rata with TOP; value = mark-out of what fills, zero if unfilled; block-bootstrap bands over five-minute blocks.")
code("for sym, q in run['queue'].items():\n    s = q['tau_10s']\n    tab = pd.DataFrame(s['groups']).T[['rule', 'variant', 'size', 'n', 'p_any_fill', 'fill_share', 'median_time_to_fill_s', 'half_spread_per_filled_lot', 'markout_per_filled_lot', 'value_per_lot', 'ci']]\n    display(tab)\n    for k in ('queue_value_fifo', 'queue_value_fifo_mid', 'queue_value_prorata_top', 'prorata_size_effect', 'fifo_vs_prorata_back'):\n        print(sym, k, s[k])\n    display(Image(f'results/figures/queue_{sym}.png'))")
md("Every real order that joined the touch, by the volume ahead of it: fill share, life, and the 10 s mark-out of the ones that filled.")
code("for sym, q in run['queue'].items():\n    ro = q['real_orders']\n    print({k: v for k, v in ro.items() if k != 'by_ahead'})\n    display(pd.DataFrame(ro['by_ahead']))")
md("## 2. The book\n\nTime-weighted spread and depth, event and trade rates by hour (UTC); the compiled replay rebuilds the book from every MBO event and the validation compares all ten levels with Databento's MBP-10 at every packet.")
code("for sym, q in run['queue'].items():\n    b = q['book']\n    print({k: v for k, v in b.items() if k != 'by_hour'})\n    display(pd.DataFrame(b['by_hour']))\n    display(Image(f'results/figures/book_{sym}.png'))")
md("## 3. The passive one-lot quoter\n\nOne lot each side at the touch, joined at the back, re-quoted when the touch moves, inventory hedged at the opposite touch when it reaches the limit. The attribution reconciles: cash + inventory at the closing mid = spread capture + inventory + hedge.")
code("for sym, m in run.get('mm', {}).items():\n    print(sym, {k: v for k, v in m.items() if k not in ('config',)})\ndisplay(Image('results/figures/mm.png'))")
md("## 4. Rolls\n\nCalendar spreads between listed contracts; implied financing of the equity spread against SOFR net of the dividend yield; the roll window; forty years of monthly energy rolls from EIA settlements.")
code("r = run['roll']\ndisplay(pd.DataFrame({k: {kk: vv for kk, vv in v.items() if not isinstance(vv, (dict, list))} for k, v in r.items()}).T)\nev = pd.concat([pd.DataFrame(v['events']) for v in r.values() if v.get('events')], ignore_index=True)\ndisplay(ev)\ndisplay(pd.DataFrame({k: v['eia']['roll_cost_long_usd'] for k, v in r.items() if 'eia' in v}).T)\ndisplay(pd.DataFrame({k: v['implied_financing'] for k, v in r.items() if 'implied_financing' in v}).T)\ndisplay(Image('results/figures/roll.png'))")
md("## 5. The stated book\n\nSettlement variation = price + roll exactly; execution at each roll (half a spread tick and two fees per contract); financing of the SPAN-style margin at SOFR; core and hedge halves.")
code("b = run['book']\nprint({k: v for k, v in b.items() if k not in ('positions', 'rolls', 'by_role_usd', 'by_root_usd')})\ndisplay(pd.DataFrame(b['by_root_usd']).T); display(pd.DataFrame(b['by_role_usd']).T); display(pd.DataFrame(b['rolls']))\ndisplay(Image('results/figures/book.png'))")
md("## 6. The monitor\n\nFault days alternate with clean days; a fault is caught when an alert of its rule for its product arrives within the detection window.")
code("m = run['monitor']\nprint({k: v for k, v in m.items() if k not in ('by_kind', 'limits', 'clean_day_alerts_by_rule')})\ndisplay(pd.DataFrame(m['by_kind']).T)\ndisplay(Image('results/figures/monitor.png'))")
md("## 7. The matching rules and SQL over the store")
code("mt = run['matching']\ndisplay(pd.DataFrame(mt['rules']).T)\nprint(json.dumps(mt['implied'], indent=1)[:1200])\nimport duckdb\ncon = duckdb.connect(os.path.join(DER, 'futdesk.duckdb'), read_only=True)\nfor q in [\"select rule, variant, size, count(*) n, avg(filled * 1.0 / size) fill_share from shadows_esz5 group by 1,2,3 order by 1,2,3\",\n          \"select root, count(*) rolls, round(avg(roll_cost_long_usd)) mean_cost from roll_eia_events group by 1\",\n          \"select rule, count(*) from monitor_alerts group by 1 order by 2 desc\"]:\n    try:\n        display(con.execute(q).df())\n    except Exception as e:\n        print('no table:', str(e)[:80])\ncon.close()")


def execute(nb):
    ns = {}
    cwd = os.getcwd(); os.chdir(ROOT)
    try:
        for n, c in enumerate(nb["cells"]):
            if c["cell_type"] != "code":
                continue
            outputs = []

            def display(obj):
                import pandas as pd
                if hasattr(obj, "filename"):
                    with open(obj.filename, "rb") as fh:
                        outputs.append({"output_type": "display_data", "metadata": {}, "data": {"image/png": base64.b64encode(fh.read()).decode()}})
                elif isinstance(obj, (pd.DataFrame, pd.Series)):
                    df = obj.to_frame() if isinstance(obj, pd.Series) else obj
                    outputs.append({"output_type": "display_data", "metadata": {}, "data": {"text/html": df.to_html(max_rows=60, max_cols=30), "text/plain": df.to_string(max_rows=60, max_cols=30)}})
                else:
                    outputs.append({"output_type": "display_data", "metadata": {}, "data": {"text/plain": repr(obj)}})

            class _Image:
                def __init__(self, filename):
                    self.filename = filename
            ns["display"] = display; ns["Image"] = _Image
            buf = io.StringIO()
            src = c["source"].replace("from IPython.display import Image, display", "")
            try:
                with contextlib.redirect_stdout(buf):
                    exec(compile(src, f"<cell {n}>", "exec"), ns)
            except Exception:
                outputs.append({"output_type": "stream", "name": "stderr", "text": traceback.format_exc()})
            if buf.getvalue():
                outputs.insert(0, {"output_type": "stream", "name": "stdout", "text": buf.getvalue()})
            c["outputs"] = outputs; c["execution_count"] = n + 1
    finally:
        os.chdir(cwd)
    return nb


if __name__ == "__main__":
    nb = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}, "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
    if "--execute" in sys.argv:
        nb = execute(nb)
        errs = [o for c in nb["cells"] for o in c.get("outputs", []) if o.get("name") == "stderr"]
        if errs:
            print("cell errors:", len(errs)); print(errs[0]["text"][-600:])
    os.makedirs(os.path.join(ROOT, "notebooks"), exist_ok=True)
    with open(os.path.join(ROOT, "notebooks", "results.ipynb"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(nb, f, indent=1)
    print("wrote notebooks/results.ipynb", "(executed)" if "--execute" in sys.argv else "")
