#!/usr/bin/env python3
"""results/summary.md from results/run.json.   python scripts/summarize.py [results_dir]"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from futdesk import data  # noqa: E402

R = sys.argv[1] if len(sys.argv) > 1 else data.RESULTS
run = json.load(open(os.path.join(R, "run.json"), encoding="utf-8"))
L = []


def f(x, d=2):
    return "—" if x is None or (isinstance(x, float) and x != x) else f"{x:,.{d}f}"


def ci(c, d=3):
    return "—" if not c or c[0] is None else f"[{c[0]:.{d}f}, {c[1]:.{d}f}]"


L.append(f"# futures-desk-analytics — results\n\nGenerated {run['generated']} in {run['seconds']:.0f} s ({'fast' if run.get('fast') else 'full'} run; compiled replay: {run.get('ext')}).\n")
L.append("## Tapes\n\n| file | symbol | events | session (UTC) | hours | book rebuilt vs MBP-10 |\n|---|---|---|---|---|---|")
for t in run["tapes"]:
    v = t.get("validation", {})
    L.append(f"| {t['file']} | {t['symbol']} | {t['events']:,} | {t['start'][:16]} → {t['end'][11:16]} | {t['hours']:.1f} | {v.get('mismatches', '—')} of {v.get('fields', 0):,} fields differ over {v.get('packets', 0):,} packets |")
L.append("")
for sym, q in run.get("queue", {}).items():
    if "tau_10s" not in q:
        L.append(f"## {sym}: {q.get('error')}\n"); continue
    b = q["book"]; s = q["tau_10s"]
    L.append(f"## {sym}: the book\n\n{b['events']:,} events, {b['orders_added']:,} orders added, {b['trades']:,} trades for {b['traded_lots']:,} lots (mean {f(b['trade_size_mean'], 1)}, p99 {f(b['trade_size_p99'], 0)}); "
             f"time-weighted spread {f(b['spread_ticks_tw'], 3)} ticks (one tick wide {b['share_time_one_tick']:.1%} of the time), depth at the touch {f(b['touch_depth_tw'], 1)} lots in {f(b['touch_orders_tw'], 1)} orders; exchange matching algorithm {q['matching_on_exchange']}.\n")
    L.append("| hour UTC | events | trades | spread (ticks) | touch depth (lots) | touch orders |\n|---|---|---|---|---|---|")
    for h in b["by_hour"]:
        if h["seconds"] > 600:
            L.append(f"| {int(h['hour']):02d} | {int(h['events']):,} | {int(h['trades']):,} | {h['spread_ticks']:.3f} | {h['touch_depth']:.1f} | {h['touch_orders']:.1f} |")
    L.append(f"\n## {sym}: queue-position value (shadow orders, 10 s mark-out)\n\n{q['n_insertions']:,} insertion times, {q['n_shadows']:,} shadow orders, {q['n_shadow_fills']:,} fills; {s['n_blocks']} five-minute bootstrap blocks, {s['n_boot']} draws.\n")
    L.append("| rule | position | size | P(any fill in 120 s) | lots filled / posted | median time to fill (s) | half-spread at fill (ticks) | mark-out per filled lot (ticks) | value per lot posted (ticks) | 95 % |\n|---|---|---|---|---|---|---|---|---|---|")
    for k, g in s["groups"].items():
        L.append(f"| {g['rule']} | {g['variant']} | {g['size']} | {g['p_any_fill']:.3f} | {f(g['fill_share'], 3)} | {f(g['median_time_to_fill_s'], 1)} | {f(g['half_spread_per_filled_lot'], 3)} | {f(g['markout_per_filled_lot'], 3)} | {g['value_per_lot']:.3f} | {ci(g['ci'])} |")
    tv = b["tick_value_usd"]
    def qv(key, label, src=s):
        v = src.get(key)
        if v:
            L.append(f"- **{label}**: {v['ticks']:.3f} ticks per lot {ci(v['ci'])} = ${v['ticks'] * tv:.2f} {ci([v['ci'][0] * tv, v['ci'][1] * tv], 2)}")
    L.append("")
    qv("queue_value_fifo", "FIFO: front of the queue minus the back (one lot)")
    qv("queue_value_fifo_mid", "FIFO: middle minus the back")
    qv("queue_value_prorata_top", "pro-rata: TOP minus the back (ten lots)")
    qv("prorata_size_effect", "pro-rata: fifty lots minus one lot, per lot")
    qv("fifo_vs_prorata_back", "back of the queue: FIFO minus pro-rata (one lot)")
    for name in ("rth", "overnight"):
        if f"tau_10s_{name}" in q:
            qv("queue_value_fifo", f"FIFO front minus back, {name} only ({q[f'tau_10s_{name}']['n_shadows']:,} shadows)", q[f"tau_10s_{name}"])
    for tau in (1, 60):
        v = q[f"tau_{tau}s"].get("queue_value_fifo")
        if v:
            L.append(f"- FIFO front minus back at a {tau} s mark-out: {v['ticks']:.3f} {ci(v['ci'])}")
    ro = q.get("real_orders", {})
    if ro:
        L.append(f"\n### {sym}: every real order posted at the touch\n\n{ro['n_orders']:,} orders (mean size {ro['mean_size']:.2f}, {ro['share_one_lot']:.0%} one lot): {ro['p_fill']:.1%} filled, {ro['p_cancel']:.1%} cancelled; median life {ro['median_life_s']:.2f} s (filled {f(ro['median_life_filled_s'])} s, cancelled {ro['median_life_cancelled_s']:.2f} s).\n")
        L.append("| lots ahead | orders | share filled | median life (s) | mean size | 10 s mark-out of the fills (ticks) |\n|---|---|---|---|---|---|")
        for r in ro["by_ahead"]:
            L.append(f"| {r['ahead_bucket']} | {int(r['n']):,} | {r['p_fill']:.3f} | {r['median_life_s']:.2f} | {r['mean_size']:.2f} | {f(r.get('markout_10s'), 3)} |")
    L.append("")
for sym, m in run.get("mm", {}).items():
    u = m["usd"]; p = m["per_passive_lot_usd"]
    L.append(f"## {sym}: the passive one-lot quoter on the tape\n\n{m['passive_fills']:,} passive fills and {m['hedges']} hedges ({m['hedge_lots']:.0f} lots) over {m['hours']:.1f} h; inventory limit {m['config'].get('inventory_limit')}, max |inventory| {m['max_abs_inventory']:.0f}.\n")
    L.append("| component | USD | per passive fill |\n|---|---|---|")
    for k in ("spread_capture", "inventory", "hedge", "fees", "total_after_fees"):
        L.append(f"| {k} | {u[k]:,.0f} | {p[k]:.2f} |")
    L.append(f"\nIdentity check (cash + inventory at the closing mid − spread capture − inventory − hedge): {m['ticks']['identity_gap']:.2e} ticks.\n")
L.append("## Rolls\n\n| product | contracts | pair-days | rolls observed | roll cost for a long, USD/contract (spread + ½ spread tick + 2 fees) | spread drift over the prior 10 bd (USD) | implied financing − SOFR, last year (bp) |\n|---|---|---|---|---|---|---|")
for root, r in run.get("roll", {}).items():
    rc = r.get("roll_cost_long_usd", {}); dr = r.get("spread_drift_10bd_usd", {}); fin = r.get("implied_financing", {})
    L.append(f"| {root} | {r['contracts']} | {r['pair_days']:,} | {r['rolls_observed']} | {f(rc.get('mean'), 0)} | {f(dr.get('mean'), 0)} | {f(fin.get('richness_bp_mean'), 0)} {ci(fin.get('richness_bp_ci'), 0) if fin else ''} |")
L.append("\n| product | event | roll date | spread | USD/contract | drift 10 bd (USD) | volume crossover (bd before the roll) | implied financing − SOFR (bp) |\n|---|---|---|---|---|---|---|---|")
for root, r in run.get("roll", {}).items():
    for e in r.get("events", []):
        L.append(f"| {root} | {e['near']}→{e['far']} | {e['roll_date']} | {e['spread']:.4f} | {e['spread_usd']:,.0f} | {f(e['drift_usd'], 0)} | {e['bd_crossover_to_roll'] if e['bd_crossover_to_roll'] is not None else '—'} | {f(e.get('richness_bp'), 0)} |")
L.append("\n### Energy rolls from the EIA settlement history\n\n| product | rolls | span | mean roll cost for a long (USD) | 95 % | mean absolute | share in contango | drift over the prior 10 bd (USD) |\n|---|---|---|---|---|---|---|---|")
for root, r in run.get("roll", {}).items():
    e = r.get("eia")
    if e:
        L.append(f"| {root} | {e['rolls']} | {e['from'][:7]} → {e['to'][:7]} | {e['roll_cost_long_usd']['mean']:,.0f} | {ci(e['roll_cost_long_usd']['ci'], 0)} | {e['abs_roll_cost_usd']['mean']:,.0f} | {e['share_contango']:.0%} | {e['drift_usd_10bd']['mean']:,.0f} {ci(e['drift_usd_10bd']['ci'], 0)} |")
bk = run.get("book", {})
if "components_usd" in bk:
    c = bk["components_usd"]
    L.append(f"\n## The stated book\n\n{bk['from']} → {bk['to']} ({bk['days']} settlement days; average notional ${bk['avg_notional_usd'] / 1e6:.0f}m, initial margin ${bk['avg_initial_margin_usd'] / 1e6:.2f}m). Positions: " + ", ".join(f"{p['qty']:+d} {p['root']} ({p['role']})" for p in bk["positions"]) + ".\n")
    L.append("| component | USD |\n|---|---|")
    for k in ("price", "roll", "variation", "execution", "financing", "total"):
        L.append(f"| {k} | {c[k]:,.0f} |")
    L.append(f"\nvariation = price + roll to {bk['identity_gap_usd']:.2e}; daily variation sd ${bk['variation_daily_sd_usd']:,.0f}, worst day ${bk['worst_day_usd']:,.0f} ({bk['worst_day']}), max drawdown ${bk['max_drawdown_usd']:,.0f}; financing is {bk['financing_share_of_gross_pnl']:.2%} of the gross daily variation.\n")
    L.append("| product | variation | price | roll | execution | rolls |\n|---|---|---|---|---|---|")
    for root, r in bk["by_root_usd"].items():
        L.append(f"| {root} | {r['variation']:,.0f} | {r['price']:,.0f} | {r['roll']:,.0f} | {r['execution']:,.0f} | {int(r['rolls'])} |")
    L.append("\n| role | variation | price | roll | execution |\n|---|---|---|---|---|")
    for role, r in bk["by_role_usd"].items():
        L.append(f"| {role} | {r['variation']:,.0f} | {r['price']:,.0f} | {r['roll']:,.0f} | {r['execution']:,.0f} |")
mo = run.get("monitor", {})
if mo:
    L.append(f"\n## The monitor\n\n{mo['fault_days']} fault days and {mo['clean_days']} clean days (simulated on real price paths): {mo['caught']} of {mo['injected']} faults caught ({mo['detection_rate']:.1%}), median time to detect {f(mo['median_ttd_s'], 1)} s; {f(mo['false_alerts_per_clean_day'], 2)} alerts per clean day, {f(mo['alerts_per_fault_day'], 1)} per fault day.\n")
    L.append("| fault | injected | caught | median time to detect (s) | rules that caught it |\n|---|---|---|---|---|")
    for k, v in mo["by_kind"].items():
        L.append(f"| {k} | {v['injected']} | {v['caught']} | {f(v['median_ttd_s'], 1)} | {', '.join(v['rules'])} |")
m = run.get("matching", {})
if m:
    L.append(f"\n## The matching rules on one level\n\nResting {m['resting']} lots (order 1 has TOP status), 60 lots incoming.\n\n| rule | " + " | ".join(f"order {i}" for i in range(1, 5)) + " |\n|---|---|---|---|---|")
    for rule, al in m["rules"].items():
        L.append(f"| {rule} | " + " | ".join(str(al.get(str(i), 0)) for i in range(1, 5)) + " |")
    im = m["implied"]
    L.append(f"\nImplied spread: leg1 {im['before']['leg1']['bid_real'][0]}/{im['before']['leg1']['ask_real'][0]}, leg2 {im['before']['leg2']['bid_real'][0]}/{im['before']['leg2']['ask_real'][0]} → implied spread bid {im['before']['spread']['bid'][0]} ({im['before']['spread']['bid'][1]} lots), ask {im['before']['spread']['ask'][0]} ({im['before']['spread']['ask'][1]}); selling 2 spreads at {im['sell_2_spread_at_7'][0]['price']} executed {len(im['leg_fills'])} leg fills and left leg1 bid {im['after']['leg1']['bid'][1]} lots, leg2 ask {im['after']['leg2']['ask'][1]} lots.\n")
with open(os.path.join(R, "summary.md"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write("\n".join(L) + "\n")
print("summary ->", os.path.join(R, "summary.md"))
