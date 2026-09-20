#!/usr/bin/env python3
"""report.pdf from results/summary.md and results/figures/*.png (fpdf2).   python scripts/report.py [results] [report.pdf]"""
import os
import sys

from fpdf import FPDF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "results")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "report.pdf")

INTRO = """Question. A futures desk on CME lives on four mechanics: the exchange's matching rules (FIFO for the index, Treasury and energy outrights; pro-rata with a TOP order for SOFR), the queue position of its passive orders, the quarterly or monthly roll of every position it carries, and a P&L that only makes sense once daily settlement variation, the roll, fees and the financing of margin are separated. What is a place in the queue worth under each matching rule, measured on recorded order-by-order depth rather than assumed; how do implied orders move an outright book; what does a roll cost per contract and how does the spread behave in the roll window; how does a stated futures book's P&L decompose; and how fast does a positions-and-limits monitor catch what goes wrong?

Method. Python package (futdesk) with a C++ replay (pybind11) and DuckDB/parquet storage. Matching engine: price-time, the Allocation algorithm (TOP order, pro-rata with a 2-lot minimum, FIFO leftover), the configurable FIFO/pro-rata split, and first-generation implied orders between two outrights and their calendar spread, with known-answer tests. Book replay: the order book rebuilt from every MBO event and checked level by level against the exchange's MBP-10 feed. Queue value: shadow orders inserted at the touch every second at the front, middle and back of the queue under FIFO and under pro-rata, carried through the recorded fills and cancels, valued at the mark-out of what fills, with block-bootstrap bands; and the outcome of every real order posted at the touch. Rolls: calendar spreads between listed contracts from daily closes, the implied financing of the equity-index spread against SOFR net of dividends, the spread and the volume migration through the roll window, and forty years of monthly energy rolls from the EIA settlement history. Attribution: the stated book on settlements (price, roll, execution, financing; core and hedge), and a passive one-lot quoter replayed on the tape (spread capture, inventory, hedge, fees), both reconciling to the cent. Monitor: thirteen rules, eleven fault kinds injected on simulated days built on real price paths, detection rate and false alerts scored.

What is real and what is simulated. The order-by-order tape is real (Databento's public CME Globex MDP 3.0 sample: one Globex session of the December 2025 E-mini S&P 500, 3.7 million events); the settlements are real daily closes per listed contract (Yahoo) and EIA's NYMEX settlement history; SOFR is the NY Fed's. The shadow orders, the quoter and the pro-rata counterfactual are simulations on that tape and assume our order would not have changed anyone else's behaviour. The monitor's days are simulated. Fees and margins are approximate public values in a config file, to be replaced by the desk's own."""

FIGS = [("queue_ESZ5.png", "Queue position on the recorded ESZ5 tape: value per lot posted by rule and place in the queue with bootstrap bands; fill probability against the mark-out of what fills; the value by mark-out horizon; and the fill share of every real order by the volume ahead of it when it joined the touch."),
        ("book_ESZ5.png", "The ESZ5 book through the session: events per hour, time-weighted depth at the touch and the time-weighted spread (one tick 95 % of the time)."),
        ("mm.png", "The passive one-lot quoter replayed on the tape: cumulative spread capture, inventory, hedge and fees; the per-fill decomposition; the inventory path."),
        ("roll.png", "Rolls: the far contract's share of volume through the roll window; the equity-index calendar spread's implied financing against SOFR net of dividends; forty years of monthly energy roll costs; the CL and NG spread through the roll window."),
        ("book.png", "The stated book on settlements: cumulative price, roll, execution and financing components; by product; the daily settlement variation."),
        ("monitor.png", "The monitor: faults injected and caught by kind, the median time to detect, alerts per day on fault and clean days."),
        ("matching.png", "The allocation rules on one price level: FIFO, pro-rata with a 2-lot minimum, the Allocation algorithm with a TOP order, and the 40/60 split.")]


class PDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 9); self.set_text_color(120); self.cell(0, 6, "futures-desk-analytics - Globex matching, queue value on recorded depth, rolls, P&L attribution, the monitor", align="R"); self.ln(8); self.set_text_color(0)

    def footer(self):
        self.set_y(-12); self.set_font("Helvetica", "", 8); self.set_text_color(120); self.cell(0, 6, f"{self.page_no()}", align="C")


def clean(s):
    return (s.replace("–", "-").replace("—", "-").replace("−", "-").replace("×", "x").replace("≥", ">=").replace("≤", "<=").replace("…", "...").replace("²", "^2").replace("±", "+/-").replace("**", "").replace("`", "")
             .replace("→", "->").replace("≈", "~").replace("½", "1/2").replace("’", "'").replace("σ", "sigma").replace("τ", "tau").replace("Δ", "d").replace("é", "e"))


def md_table(pdf, rows):
    cols = [c.strip() for c in rows[0].strip("|").split("|")]
    body = [[clean(c.strip()) for c in r.strip("|").split("|")] for r in rows[2:]]
    n = len(cols); w = (pdf.w - 20) / n; fs = 6.5 if n <= 7 else 5.0 if n <= 12 else 4.2; cut = 42 if n <= 7 else 22 if n <= 12 else 14
    pdf.set_font("Helvetica", "B", fs)
    for c in cols:
        pdf.cell(w, 5, clean(c)[:cut], border=1)
    pdf.ln(5); pdf.set_font("Helvetica", "", fs)
    for r in body[:80]:
        if pdf.get_y() > pdf.h - 20:
            pdf.add_page()
        for c in r:
            pdf.cell(w, 4.5, c[:cut], border=1)
        pdf.ln(4.5)
    pdf.ln(2)


def main():
    pdf = PDF(); pdf.set_auto_page_break(auto=True, margin=15); pdf.add_page()
    pdf.set_font("Helvetica", "B", 16); pdf.cell(0, 10, "Futures Desk Microstructure and Analytics on CME", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for para in INTRO.split("\n\n"):
        pdf.multi_cell(0, 4.5, clean(para)); pdf.ln(2)
    for fn, cap in FIGS:
        p = os.path.join(R, "figures", fn)
        if not os.path.exists(p):
            continue
        if pdf.get_y() > pdf.h - 90:
            pdf.add_page()
        pdf.image(p, w=pdf.w - 20); pdf.set_font("Helvetica", "I", 8); pdf.multi_cell(0, 4, clean(cap)); pdf.ln(3); pdf.set_font("Helvetica", "", 9)
    sm = os.path.join(R, "summary.md")
    if os.path.exists(sm):
        pdf.add_page(); lines = open(sm, encoding="utf-8").read().splitlines(); i = 0
        while i < len(lines):
            l = lines[i]
            if l.startswith("## ") or l.startswith("### "):
                pdf.set_font("Helvetica", "B", 11 if l.startswith("## ") else 10); pdf.ln(2); pdf.cell(0, 7, clean(l.lstrip("# ")), new_x="LMARGIN", new_y="NEXT"); pdf.set_font("Helvetica", "", 9); i += 1
            elif l.startswith("|"):
                j = i
                while j < len(lines) and lines[j].startswith("|"):
                    j += 1
                if j - i >= 2:
                    md_table(pdf, lines[i:j])
                i = j
            elif l.startswith("# "):
                i += 1
            elif l.strip():
                pdf.set_x(pdf.l_margin); pdf.multi_cell(0, 4.5, clean(l.strip().lstrip("- "))); i += 1
            else:
                i += 1
    pdf.output(OUT); print("wrote", OUT)


if __name__ == "__main__":
    main()
