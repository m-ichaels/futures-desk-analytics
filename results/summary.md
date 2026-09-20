# futures-desk-analytics — results

Generated 2026-09-19T07:10:25+00:00 in 61 s (full run; compiled replay: True).

## Tapes

| file | symbol | events | session (UTC) | hours | book rebuilt vs MBP-10 |
|---|---|---|---|---|---|
| mbo_glbx-mdp3-sample.parquet | ESZ5 | 3,667,804 | 2025-09-22T00:00 → 15:59 | 16.0 | 67 of 147,275,160 fields differ over 2,454,586 packets |

## ESZ5: the book

3,658,972 events, 1,430,012 orders added, 153,819 trades for 493,504 lots (mean 3.2, p99 28); time-weighted spread 1.045 ticks (one tick wide 95.5% of the time), depth at the touch 24.3 lots in 15.3 orders; exchange matching algorithm F.

| hour UTC | events | trades | spread (ticks) | touch depth (lots) | touch orders |
|---|---|---|---|---|---|
| 00 | 72,810 | 3,375 | 1.076 | 13.3 | 10.0 |
| 01 | 59,514 | 2,738 | 1.057 | 13.3 | 9.8 |
| 02 | 31,022 | 1,345 | 1.041 | 14.7 | 9.9 |
| 03 | 30,885 | 1,433 | 1.042 | 17.5 | 12.1 |
| 04 | 20,841 | 1,067 | 1.078 | 17.7 | 11.3 |
| 05 | 29,761 | 1,393 | 1.060 | 15.4 | 10.2 |
| 06 | 61,822 | 3,220 | 1.081 | 16.2 | 11.2 |
| 07 | 176,401 | 7,147 | 1.099 | 16.1 | 10.9 |
| 08 | 114,844 | 4,975 | 1.033 | 17.3 | 11.7 |
| 09 | 93,209 | 3,516 | 1.034 | 14.1 | 10.3 |
| 10 | 71,178 | 3,265 | 1.029 | 17.5 | 11.9 |
| 11 | 90,140 | 4,305 | 1.027 | 16.9 | 11.1 |
| 12 | 123,473 | 6,518 | 1.036 | 21.2 | 14.0 |
| 13 | 899,842 | 37,636 | 1.023 | 42.8 | 25.8 |
| 14 | 992,944 | 41,029 | 1.002 | 66.7 | 37.3 |
| 15 | 790,286 | 30,857 | 1.002 | 67.4 | 36.8 |

## ESZ5: queue-position value (shadow orders, 10 s mark-out)

57,540 insertion times, 402,780 shadow orders, 1,836,867 fills; 192 five-minute bootstrap blocks, 1000 draws.

| rule | position | size | P(any fill in 120 s) | lots filled / posted | median time to fill (s) | half-spread at fill (ticks) | mark-out per filled lot (ticks) | value per lot posted (ticks) | 95 % |
|---|---|---|---|---|---|---|---|---|---|
| fifo | back | 1 | 0.880 | 0.880 | 9.3 | 0.313 | 0.169 | 0.149 | [0.118, 0.181] |
| fifo | mid | 1 | 0.893 | 0.893 | 7.1 | 0.391 | 0.218 | 0.195 | [0.166, 0.225] |
| fifo | front | 1 | 0.946 | 0.946 | 3.4 | 0.501 | 0.353 | 0.334 | [0.313, 0.355] |
| prorata | back | 1 | 0.868 | 0.868 | 9.7 | 0.278 | 0.123 | 0.107 | [0.074, 0.138] |
| prorata | back | 10 | 0.876 | 0.825 | 14.5 | 0.105 | -0.070 | -0.058 | [-0.082, -0.032] |
| prorata | back | 50 | 0.896 | 0.701 | 29.0 | -0.244 | -0.411 | -0.288 | [-0.323, -0.255] |
| prorata | front | 10 | 0.940 | 0.877 | 8.9 | 0.298 | 0.112 | 0.098 | [0.074, 0.122] |

- **FIFO: front of the queue minus the back (one lot)**: 0.185 ticks per lot [0.164, 0.206] = $2.31 [2.05, 2.57]
- **FIFO: middle minus the back**: 0.046 ticks per lot [0.039, 0.053] = $0.58 [0.48, 0.67]
- **pro-rata: TOP minus the back (ten lots)**: 0.156 ticks per lot [0.145, 0.167] = $1.95 [1.81, 2.08]
- **pro-rata: fifty lots minus one lot, per lot**: -0.394 ticks per lot [-0.430, -0.361] = $-4.93 [-5.37, -4.52]
- **back of the queue: FIFO minus pro-rata (one lot)**: 0.042 ticks per lot [0.034, 0.052] = $0.53 [0.42, 0.65]
- **FIFO front minus back, rth only (63,000 shadows)**: 0.055 ticks per lot [-0.005, 0.105] = $0.68 [-0.06, 1.31]
- **FIFO front minus back, overnight only (339,780 shadows)**: 0.209 ticks per lot [0.188, 0.231] = $2.61 [2.35, 2.88]
- FIFO front minus back at a 1 s mark-out: 0.196 [0.178, 0.214]
- FIFO front minus back at a 60 s mark-out: 0.195 [0.171, 0.221]

### ESZ5: every real order posted at the touch

1,044,780 orders (mean size 1.82, 66% one lot): 26.9% filled, 72.8% cancelled; median life 0.17 s (filled 0.49 s, cancelled 0.12 s).

| lots ahead | orders | share filled | median life (s) | mean size | 10 s mark-out of the fills (ticks) |
|---|---|---|---|---|---|
| 0-0 | 42,980 | 0.714 | 0.00 | 2.16 | 0.037 |
| 1-4 | 86,247 | 0.507 | 0.02 | 1.84 | 0.042 |
| 5-9 | 115,888 | 0.367 | 0.17 | 1.73 | 0.044 |
| 10-19 | 191,908 | 0.292 | 0.22 | 1.79 | 0.043 |
| 20-49 | 363,514 | 0.213 | 0.14 | 1.80 | 0.022 |
| 50-99 | 224,627 | 0.126 | 0.31 | 1.83 | 0.019 |
| 100+ | 19,616 | 0.128 | 0.43 | 1.90 | 0.021 |

## ESZ5: the passive one-lot quoter on the tape

42,946 passive fills and 1026 hedges (5130 lots) over 16.0 h; inventory limit 5, max |inventory| 5.

| component | USD | per passive fill |
|---|---|---|
| spread_capture | 127,369 | 2.97 |
| inventory | -105,694 | -2.46 |
| hedge | -43,500 | -1.01 |
| fees | -69,710 | -1.62 |
| total_after_fees | -91,535 | -2.13 |

Identity check (cash + inventory at the closing mid − spread capture − inventory − hedge): 0.00e+00 ticks.

## Rolls

| product | contracts | pair-days | rolls observed | roll cost for a long, USD/contract (spread + ½ spread tick + 2 fees) | spread drift over the prior 10 bd (USD) | implied financing − SOFR, last year (bp) |
|---|---|---|---|---|---|---|
| ES | 5 | 2,000 | 1 | 3,392 | -12 | 51 [43, 58] |
| NQ | 5 | 1,005 | 1 | 5,948 | 25 | 39 [33, 46] |
| ZN | 3 | 190 | 1 | -244 | 16 | —  |
| ZB | 3 | 190 | 1 | -490 | 0 | —  |
| ZT | 3 | 176 | 1 | -268 | -47 | —  |
| SR3 | 13 | 12 | 0 | — | — | —  |
| ZQ | 18 | 8,518 | 0 | — | — | —  |
| CL | 35 | 17,035 | 1 | -4,212 | -1,310 | —  |
| NG | 36 | 17,539 | 0 | — | — | —  |
| GC | 14 | 3,498 | 0 | — | — | —  |

| product | event | roll date | spread | USD/contract | drift 10 bd (USD) | volume crossover (bd before the roll) | implied financing − SOFR (bp) |
|---|---|---|---|---|---|---|---|
| ES | ESU26→ESZ26 | 2026-09-11 | 67.7500 | 3,388 | -12 | -1 | 91 |
| NQ | NQU26→NQZ26 | 2026-09-11 | 297.2500 | 5,945 | 25 | -1 | 86 |
| ZN | ZNU26→ZNZ26 | 2026-08-27 | -0.2500 | -250 | 16 | 0 | — |
| ZB | ZBU26→ZBZ26 | 2026-08-27 | -0.5000 | -500 | 0 | 0 | — |
| ZT | ZTU26→ZTZ26 | 2026-08-27 | -0.1367 | -273 | -47 | 0 | — |
| CL | CLV26→CLX26 | 2026-09-18 | -4.2200 | -4,220 | -1,310 | 0 | — |

### Energy rolls from the EIA settlement history

| product | rolls | span | mean roll cost for a long (USD) | 95 % | mean absolute | share in contango | drift over the prior 10 bd (USD) |
|---|---|---|---|---|---|---|---|
| CL | 471 | 1985-01 → 2024-03 | 38 | [-48, 124] | 595 | 57% | -17 [-60, 28] |
| NG | 363 | 1994-01 → 2024-03 | 726 | [519, 942] | 1,244 | 80% | -205 [-340, -81] |

## The stated book

2026-08-19 → 2026-09-18 (22 settlement days; average notional $48m, initial margin $1.45m). Positions: +40 ES (core), -20 NQ (hedge), +100 ZN (core), -30 ZB (hedge), +25 CL (core), +10 GC (core).

| component | USD |
|---|---|
| price | 48,713 |
| roll | -6,600 |
| variation | 42,113 |
| execution | -1,080 |
| financing | -4,564 |
| total | 36,469 |

variation = price + roll to 0.00e+00; daily variation sd $96,773, worst day $-158,950 (2026-09-04), max drawdown $-345,563; financing is 0.26% of the gross daily variation.

| product | variation | price | roll | execution | rolls |
|---|---|---|---|---|---|
| CL | 406,000 | 406,000 | 0 | 0 | 0 |
| ES | -138,500 | -3,000 | -135,500 | -166 | 1 |
| GC | 2,900 | 2,900 | 0 | 0 | 0 |
| NQ | -13,600 | -132,500 | 118,900 | -68 | 1 |
| ZB | 33,750 | 48,750 | -15,000 | -285 | 1 |
| ZN | -248,438 | -273,438 | 25,000 | -561 | 1 |

| role | variation | price | roll | execution |
|---|---|---|---|---|
| core | 21,963 | 132,463 | -110,500 | -727 |
| hedge | 20,150 | -83,750 | 103,900 | -353 |

## The monitor

20 fault days and 20 clean days (simulated on real price paths): 116 of 120 faults caught (96.7%), median time to detect 0.0 s; 0.00 alerts per clean day, 73.3 per fault day.

| fault | injected | caught | median time to detect (s) | rules that caught it |
|---|---|---|---|---|
| fat_finger | 10 | 10 | 0.0 | FAT_FINGER |
| wrong_side | 11 | 11 | 0.0 | POSITION_JUMP |
| duplicate_fill | 12 | 12 | 5.0 | DUPLICATE_FILL |
| bad_price | 13 | 13 | 0.0 | PRICE_BAND |
| stale_feed | 11 | 11 | 128.5 | STALE_MARK |
| missed_roll | 11 | 11 | 0.0 | DELIVERY_RISK, EXPIRY_RISK |
| margin_shock | 13 | 13 | 9.5 | MARGIN_CALL |
| hedge_dropped | 14 | 11 | 0.0 | PAIR_BROKEN |
| limit_breach | 11 | 11 | 0.0 | POSITION_LIMIT |
| phantom_fill | 8 | 8 | 13,893.0 | RECON_BREAK |
| drawdown | 6 | 5 | 1.7 | PNL_DRAWDOWN |

## The matching rules on one level

Resting [100, 50, 30, 1] lots (order 1 has TOP status), 60 lots incoming.

| rule | order 1 | order 2 | order 3 | order 4 |
|---|---|---|---|---|
| FIFO | 60 | 0 | 0 | 0 |
| pro-rata, 2-lot minimum, no TOP | 35 | 16 | 9 | 0 |
| Allocation: TOP (max 10) + pro-rata + FIFO leftover | 38 | 14 | 8 | 0 |
| split 40 % FIFO / 60 % pro-rata | 43 | 11 | 6 | 0 |

Implied spread: leg1 1000/1002, leg2 990/993 → implied spread bid 7 (6 lots), ask 12 (4); selling 2 spreads at 7 executed 2 leg fills and left leg1 bid 8 lots, leg2 ask 4 lots.

