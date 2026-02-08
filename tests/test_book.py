"""The rebuilt book: Python against C++ event for event, both against the exchange's MBP-10; shadow orders against
hand-worked fills on tiny tapes."""
import os

import numpy as np
import pandas as pd
import pytest

from futdesk import book, data, products, queue, run

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "data", "sample", "derived")
ACT = {"N": 0, "A": 1, "C": 2, "M": 3, "F": 4, "T": 5, "R": 6}


def tape(events):
    """events: (t_seconds, action, side, price_ticks, size, order_id, last)"""
    e = list(events)
    return {"ts": np.array([int(x[0] * 1e9) for x in e], dtype=np.int64), "action": np.array([ACT[x[1]] for x in e], dtype=np.int8), "side": np.array([x[2] for x in e], dtype=np.int8),
            "price": np.array([x[3] for x in e], dtype=np.int64), "size": np.array([x[4] for x in e], dtype=np.int32), "order_id": np.array([x[5] for x in e], dtype=np.int64),
            "flags": np.array([128 if x[6] else 0 for x in e], dtype=np.int16), "sequence": np.arange(len(e), dtype=np.int64)}


def shadows(arr, specs, insert_at_s, horizon_s=100.0, prorata_min=2, top_max=10**6):
    out = book._ext.run_shadows(arr["ts"], arr["action"], arr["side"], arr["price"], arr["size"], arr["order_id"], arr["flags"], specs, int(1e15), int(horizon_s * 1e9), int(insert_at_s * 1e9), int(1e18), prorata_min, top_max, False, False, 5, 1, 0)
    sh = pd.DataFrame({k: np.asarray(v) for k, v in out["shadows"].items()}); fi = pd.DataFrame({k: np.asarray(v) for k, v in out["fills"].items()})
    return sh, fi


@pytest.fixture(scope="module")
def sample():
    df = pd.read_parquet(os.path.join(SAMPLE, "mbo_sample_es.parquet"))
    return data.mbo_arrays(df, products.spec("ES")["tick"])


def test_python_book_modify_and_fill_rules():
    b = book.Book()
    b.add(1, 1, 100, 5); b.add(2, 1, 100, 3); b.add(3, -1, 101, 4)
    assert b.best(1) == 100 and b.best(-1) == 101 and b.depth(1) == [(100, 8, 2)]
    b.modify(1, 1, 100, 2); assert [o for o, _ in b.queue(1, 100)] == [1, 2]          # size decrease keeps its place
    b.modify(1, 1, 100, 6); assert [o for o, _ in b.queue(1, 100)] == [2, 1]          # increase goes to the back
    b.fill(2, 3); assert b.queue(1, 100) == [(1, 6)]
    b.fill(1, 2); assert b.queue(1, 100) == [(1, 4)] and b.total[1][100] == 4
    b.cancel(1); assert b.best(1) is None


@pytest.mark.skipif(not book.HAVE_EXT, reason="compiled replay not built")
def test_cpp_matches_python_on_sample(sample):
    sub = {k: v[:150000] for k, v in sample.items()}
    a = book._ext.replay_top(sub["action"], sub["side"], sub["price"], sub["size"], sub["order_id"], sub["flags"], 10, True)
    ext = book._ext; book._ext = None
    try:
        b = book.replay_top(sub, 10, True)
    finally:
        book._ext = ext
    for k in ("idx", "bid_px", "bid_sz", "bid_ct", "ask_px", "ask_sz", "ask_ct"):
        assert np.array_equal(np.asarray(a[k]), np.asarray(b[k])), k


def test_book_matches_exchange_mbp10(sample):
    v = run.validate_book(sample, os.path.join(SAMPLE, "book_sample_es.mbp10.parquet"), products.spec("ES")["tick"])
    assert v["packets"] > 50000 and v["mismatches"] == 0


@pytest.mark.skipif(not book.HAVE_EXT, reason="compiled replay not built")
def test_fifo_shadows_front_and_back():
    arr = tape([(0, "A", 1, 100, 5, 1, 0), (0, "A", 1, 100, 3, 2, 0), (0, "A", -1, 101, 4, 3, 1),
                (1, "N", 0, 0, 0, 0, 1),                      # the insertion happens here: front and back one-lot shadows at bid 100
                (2, "T", -1, 100, 4, 0, 0), (2, "F", 1, 100, 4, 1, 1),   # seller hits: 4 lots of order 1 -> the front shadow would have taken the first lot
                (3, "F", 1, 100, 1, 1, 1),                    # order 1 done: back shadow has 3 ahead (order 2)
                (4, "C", 1, 100, 3, 2, 1),                    # order 2 cancels: nothing ahead of the back shadow
                (5, "A", 1, 100, 2, 4, 1),                    # a new order behind it
                (6, "F", 1, 100, 2, 4, 1)])                   # its fill would have been ours
    sh, fi = shadows(arr, [(0, 2, 1), (0, 0, 1)], insert_at_s=1)
    assert len(sh) == 2 and list(sh["variant"]) == [2, 0] and list(sh["queue_at_insert"]) == [8, 8]
    assert list(sh["filled"]) == [1, 1]
    assert list(fi.sort_values("id")["event"]) == [5, 9]        # front at the first fill event, back at the last


@pytest.mark.skipif(not book.HAVE_EXT, reason="compiled replay not built")
def test_shadow_swept_when_price_trades_through():
    arr = tape([(0, "A", 1, 100, 5, 1, 0), (0, "A", 1, 99, 3, 5, 0), (0, "A", -1, 101, 4, 3, 1), (1, "N", 0, 0, 0, 0, 1),
                (2, "T", -1, 99, 6, 0, 0), (2, "F", 1, 100, 5, 1, 0), (2, "F", 1, 99, 1, 5, 1)])
    sh, fi = shadows(arr, [(0, 0, 1)], insert_at_s=1)
    assert list(sh["filled"]) == [1] and int(fi["event"].iloc[0]) == 6          # the fill at our level clears the queue ahead; the print below sweeps us


@pytest.mark.skipif(not book.HAVE_EXT, reason="compiled replay not built")
def test_shadow_expires_unfilled():
    arr = tape([(0, "A", 1, 100, 5, 1, 1), (1, "N", 0, 0, 0, 0, 1), (50, "N", 0, 0, 0, 0, 1), (200, "F", 1, 100, 5, 1, 1)])
    sh, fi = shadows(arr, [(0, 2, 1)], insert_at_s=1, horizon_s=100)
    assert list(sh["filled"]) == [0] and len(fi) == 0


@pytest.mark.skipif(not book.HAVE_EXT, reason="compiled replay not built")
def test_prorata_allocation_on_tape():
    # level: 100 + 50 resting, shadow of 10 at the back; 60 lots hit the level: floor(60*10/160) = 3 for the shadow, the 2-lot leftover goes FIFO to order 1
    arr = tape([(0, "A", 1, 100, 100, 1, 0), (0, "A", 1, 100, 50, 2, 1), (1, "N", 0, 0, 0, 0, 1), (2, "T", -1, 100, 60, 0, 0), (2, "F", 1, 100, 60, 1, 1)])
    sh, fi = shadows(arr, [(1, 0, 10), (1, 2, 10), (1, 0, 1)], insert_at_s=1)
    assert list(sh["filled"]) == [3, 10, 0]                   # back 10 -> 3; TOP -> 10 first; one lot below the 2-lot minimum -> nothing
    # a sweep larger than the level fills everything
    arr = tape([(0, "A", 1, 100, 10, 1, 1), (1, "N", 0, 0, 0, 0, 1), (2, "T", -1, 100, 40, 0, 0), (2, "F", 1, 100, 10, 1, 1)])
    sh, fi = shadows(arr, [(1, 0, 10)], insert_at_s=1)
    assert list(sh["filled"]) == [10]


@pytest.mark.skipif(not book.HAVE_EXT, reason="compiled replay not built")
def test_queue_study_runs_on_sample(sample):
    top = queue.top_series(sample)
    res = queue.run(sample, insert_every_s=10.0, start_offset_s=5.0)
    fills = queue.fill_markouts(res, sample, top)
    s = queue.summarise(res, fills, n_boot=50)
    assert set(s["groups"]) >= {"fifo_back_1", "fifo_front_1", "prorata_back_10"}
    assert s["groups"]["fifo_front_1"]["p_any_fill"] >= s["groups"]["fifo_back_1"]["p_any_fill"]
    ro = queue.real_order_outcomes(res, sample, top)
    assert ro["n_orders"] > 1000 and 0 < ro["p_fill"] < 1
    b = queue.book_stats(sample, top, 12.5)
    assert b["spread_ticks_tw"] >= 1.0 and b["trades"] > 0
