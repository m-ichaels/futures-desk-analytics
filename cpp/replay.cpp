// MBO book replay with shadow orders: the queue-position study (FIFO and pro-rata counterfactuals), the outcomes of
// every real order posted at the touch, and a passive one-lot market maker with a hedge rule, all in one pass over
// the recorded events.  Python reference: futdesk/book.py and futdesk/queue.py (tests compare the two).
#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>
#include <map>
#include <unordered_map>
#include <vector>
#include <deque>
#include <algorithm>
#include <cstdint>
#include <cstddef>
#include <cmath>
using ssize_t = std::ptrdiff_t;

namespace py = pybind11;

enum Action { NONE = 0, ADD = 1, CANCEL = 2, MODIFY = 3, FILL = 4, TRADE = 5, CLEAR = 6 };

struct Order { int8_t side; int64_t price; int32_t size; int64_t prio; };
struct Level { std::map<int64_t, int64_t> q; int64_t total = 0; };   // prio -> order id, FIFO order

struct Book {
    std::unordered_map<int64_t, Order> orders;
    std::map<int64_t, Level> bids, asks;      // price -> level
    int64_t prio = 0;

    std::map<int64_t, Level>& side_map(int8_t s) { return s == 1 ? bids : asks; }
    void clear() { orders.clear(); bids.clear(); asks.clear(); }
    void add_level(int64_t oid, int8_t side, int64_t price, int32_t size, int64_t pr) {
        Level& lv = side_map(side)[price]; lv.q[pr] = oid; lv.total += size;
    }
    void remove_level(int64_t oid, const Order& o) {
        auto& m = side_map(o.side); auto it = m.find(o.price); if (it == m.end()) return;
        it->second.q.erase(o.prio); it->second.total -= o.size;
        if (it->second.q.empty()) m.erase(it);
    }
    void add(int64_t oid, int8_t side, int64_t price, int32_t size) {
        auto it = orders.find(oid);
        if (it != orders.end()) { remove_level(oid, it->second); }
        Order o{side, price, size, ++prio}; orders[oid] = o; add_level(oid, side, price, size, o.prio);
    }
    bool cancel(int64_t oid, Order& out) {
        auto it = orders.find(oid); if (it == orders.end()) return false;
        out = it->second; remove_level(oid, out); orders.erase(it); return true;
    }
    // returns: 0 = unknown (added), 1 = size decrease in place, 2 = re-queued (lost priority)
    int modify(int64_t oid, int8_t side, int64_t price, int32_t size, Order& before) {
        auto it = orders.find(oid);
        if (it == orders.end()) { add(oid, side, price, size); return 0; }
        before = it->second;
        if (price == before.price && side == before.side && size <= before.size) {
            side_map(side)[price].total += size - before.size; it->second.size = size; return 1;
        }
        remove_level(oid, before); it->second = Order{side, price, size, ++prio}; add_level(oid, side, price, size, it->second.prio); return 2;
    }
    bool fill(int64_t oid, int32_t size, Order& before) {
        auto it = orders.find(oid); if (it == orders.end()) return false;
        before = it->second;
        int32_t left = before.size - size;
        if (left <= 0) { remove_level(oid, before); orders.erase(it); }
        else { side_map(before.side)[before.price].total -= size; it->second.size = left; }
        return true;
    }
    bool best(int8_t side, int64_t& price, int64_t& total) const {
        if (side == 1) { if (bids.empty()) return false; auto it = bids.rbegin(); price = it->first; total = it->second.total; return true; }
        if (asks.empty()) return false; auto it = asks.begin(); price = it->first; total = it->second.total; return true;
    }
};

// ---------------------------------------------------------------------------------------------------------------------
py::dict replay_top(py::array_t<int8_t> action, py::array_t<int8_t> side, py::array_t<int64_t> price, py::array_t<int32_t> size,
                    py::array_t<int64_t> order_id, py::array_t<int16_t> flags, int levels, bool at_flags_last) {
    auto ac = action.unchecked<1>(); auto sd = side.unchecked<1>(); auto px = price.unchecked<1>(); auto sz = size.unchecked<1>();
    auto oid = order_id.unchecked<1>(); auto fl = flags.unchecked<1>();
    ssize_t n = ac.shape(0);
    Book book; Order tmp;
    std::vector<int64_t> idx, bp, bs, bc, ap, asz, acn;
    for (ssize_t i = 0; i < n; ++i) {
        switch (ac(i)) {
            case ADD: book.add(oid(i), sd(i), px(i), sz(i)); break;
            case CANCEL: book.cancel(oid(i), tmp); break;
            case MODIFY: book.modify(oid(i), sd(i), px(i), sz(i), tmp); break;
            case FILL: book.fill(oid(i), sz(i), tmp); break;
            case CLEAR: book.clear(); break;
            default: break;
        }
        if (at_flags_last && !(fl(i) & 128)) continue;
        idx.push_back(i);
        int k = 0;
        for (auto it = book.bids.rbegin(); it != book.bids.rend() && k < levels; ++it, ++k) { bp.push_back(it->first); bs.push_back(it->second.total); bc.push_back((int64_t)it->second.q.size()); }
        for (; k < levels; ++k) { bp.push_back(0); bs.push_back(0); bc.push_back(0); }
        k = 0;
        for (auto it = book.asks.begin(); it != book.asks.end() && k < levels; ++it, ++k) { ap.push_back(it->first); asz.push_back(it->second.total); acn.push_back((int64_t)it->second.q.size()); }
        for (; k < levels; ++k) { ap.push_back(0); asz.push_back(0); acn.push_back(0); }
    }
    auto mk = [&](std::vector<int64_t>& v, bool two) {
        py::array_t<int64_t> a = two ? py::array_t<int64_t>({(ssize_t)idx.size(), (ssize_t)levels}) : py::array_t<int64_t>((ssize_t)v.size());
        std::copy(v.begin(), v.end(), a.mutable_data()); return a;
    };
    py::dict out;
    out["idx"] = mk(idx, false); out["bid_px"] = mk(bp, true); out["bid_sz"] = mk(bs, true); out["bid_ct"] = mk(bc, true);
    out["ask_px"] = mk(ap, true); out["ask_sz"] = mk(asz, true); out["ask_ct"] = mk(acn, true);
    return out;
}

// ---------------------------------------------------------------------------------------------------------------------
// Shadow orders.  variant: 0 back, 1 mid, 2 front (TOP under pro-rata).  rule: 0 FIFO, 1 pro-rata, 2 the market maker's FIFO quotes.
struct Shadow {
    int32_t id; int8_t side; int64_t price; double prio; int32_t size, filled; int64_t ahead; int8_t rule, variant;
    int64_t t_insert, expire; bool alive; int64_t queue_at_insert;
};
struct ShadowFill { int32_t id; int64_t event; int32_t qty; int64_t price; };

struct Params { int64_t insert_every_ns; int64_t horizon_ns; int64_t start_ns; int64_t end_ns; int prorata_min; int top_max; };

struct ShadowSpec { int8_t rule, variant; int32_t size; };

struct Replay {
    Book book;
    std::vector<Shadow> shadows;
    std::vector<ShadowFill> fills;
    std::map<int64_t, std::vector<int32_t>> live_bid, live_ask;     // price -> shadow ids
    std::deque<int32_t> by_expiry;
    // real-order outcomes
    struct RealOrder { int64_t t_add; int64_t ahead; int8_t side; int64_t price; int32_t size; };
    std::unordered_map<int64_t, RealOrder> touch_orders;
    std::vector<int64_t> ro_t_add, ro_ahead, ro_t_end, ro_price; std::vector<int32_t> ro_size, ro_outcome; std::vector<int8_t> ro_side;   // outcome: 1 filled, 0 cancelled, 2 left
    // market maker
    bool mm_on = false; int32_t mm_bid = -1, mm_ask = -1; int32_t mm_inv = 0; int mm_limit = 5; int mm_size = 1;
    std::vector<int64_t> mm_t, mm_price; std::vector<int32_t> mm_qty; std::vector<int8_t> mm_side, mm_kind;   // kind 0 passive, 1 hedge
    int64_t mm_requote_after = 0; int64_t mm_hold_ns = 0;

    std::map<int64_t, std::vector<int32_t>>& live(int8_t s) { return s == 1 ? live_bid : live_ask; }

    void record_fill(Shadow& s, int64_t ev, int32_t qty) {
        if (qty <= 0) return;
        fills.push_back({s.id, ev, qty, s.price}); s.filled += qty;
        if (s.filled >= s.size) kill(s);
    }
    void kill(Shadow& s) {
        if (!s.alive) return; s.alive = false;
        auto& v = live(s.side)[s.price]; v.erase(std::remove(v.begin(), v.end(), s.id), v.end());
        if (v.empty()) live(s.side).erase(s.price);
    }
    int32_t insert_shadow(int8_t side, int64_t price, const Level& lv, ShadowSpec spec, int64_t ts, int64_t horizon, bool track_expiry = true) {
        Shadow s; s.id = (int32_t)shadows.size(); s.side = side; s.price = price; s.size = spec.size; s.filled = 0; s.rule = spec.rule; s.variant = spec.variant;
        s.t_insert = ts; s.expire = ts + horizon; s.alive = true; s.queue_at_insert = lv.total;
        if (spec.variant == 2) { s.prio = lv.q.empty() ? 0.5 : (double)lv.q.begin()->first - 0.5; s.ahead = 0; }
        else if (spec.variant == 0) { s.prio = lv.q.empty() ? 0.5 : (double)lv.q.rbegin()->first + 0.5; s.ahead = lv.total; }
        else {
            int64_t cum = 0, half = lv.total / 2; double pr = 0.5;
            for (auto& kv : lv.q) { int32_t sz = book.orders[kv.second].size; cum += sz; pr = (double)kv.first + 0.5; if (cum >= half) break; }
            s.prio = pr; s.ahead = cum;
        }
        shadows.push_back(s); live(side)[price].push_back(s.id); if (track_expiry) by_expiry.push_back(s.id); return s.id;
    }
    // pro-rata allocation of volume V at a level to the shadow s (Allocation algorithm: TOP, pro-rata with a minimum, FIFO leftover)
    int32_t prorata_alloc(const Shadow& s, const Level& lv, int64_t V, int prorata_min, int top_max) {
        int32_t rem = s.size - s.filled; if (rem <= 0 || V <= 0) return 0;
        int64_t got = 0;
        if (s.variant == 2) { got = std::min<int64_t>(std::min<int64_t>(rem, V), top_max); V -= got; if (V <= 0 || got >= rem) return (int32_t)got; }
        int64_t S = lv.total + (s.variant == 2 ? 0 : rem);   // the TOP order is excluded from the pro-rata pool
        if (S <= 0) return (int32_t)got;
        int64_t sum_alloc = 0, my_alloc = 0;
        if (s.variant != 2) { my_alloc = (V * rem) / S; if (my_alloc < prorata_min) my_alloc = 0; my_alloc = std::min<int64_t>(my_alloc, rem); sum_alloc += my_alloc; }
        std::vector<std::pair<double, int64_t>> remaining;   // prio, remaining after pro-rata, for the leftover pass
        for (auto& kv : lv.q) {
            int32_t sz = book.orders[kv.second].size; int64_t a = (V * sz) / S; if (a < prorata_min) a = 0; a = std::min<int64_t>(a, sz); sum_alloc += a;
            remaining.push_back({(double)kv.first, sz - a});
        }
        int64_t L = V - sum_alloc; got += my_alloc;
        if (L > 0 && got < rem) {           // FIFO leftover: orders ahead of the shadow take theirs first
            for (auto& r : remaining) { if (r.first > s.prio) break; L -= r.second; if (L <= 0) break; }
            if (L > 0) got += std::min<int64_t>(L, rem - got);
        }
        return (int32_t)got;
    }
    // a trade summary (the aggressor's side and size at a price, recorded before the fills it causes): the pro-rata
    // counterfactual allocates it over the pre-trade level; an aggressor larger than the level sweeps the shadow entirely
    void on_trade(int8_t aggressor_side, int64_t price, int64_t V, int64_t ev, int prorata_min, int top_max) {
        int8_t side = -aggressor_side; if (side == 0) return;
        auto& lm = live(side); auto it = lm.find(price); if (it == lm.end()) return;
        auto& m = book.side_map(side); auto lit = m.find(price); static Level empty; const Level& lv = lit == m.end() ? empty : lit->second;
        std::vector<int32_t> ids = it->second;
        for (int32_t id : ids) {
            Shadow& s = shadows[id]; if (!s.alive || s.rule != 1) continue;
            int32_t rem = s.size - s.filled;
            int32_t q = (V >= lv.total + rem) ? rem : prorata_alloc(s, lv, V, prorata_min, top_max);
            record_fill(s, ev, q);
        }
    }
    // a fill of resting order o (size qty) at its level: shadows behind it lose ahead volume, shadows ahead of it (FIFO) are filled;
    // shadows at better prices on the same side (the aggressor swept past them) are filled outright.
    void on_fill(const Order& o, int32_t qty, int64_t ev) {
        auto& lm = live(o.side);
        auto it = lm.find(o.price);
        if (it != lm.end()) {
            std::vector<int32_t> ids = it->second;
            for (int32_t id : ids) {
                Shadow& s = shadows[id]; if (!s.alive) continue;
                if (s.rule != 1) {
                    if (s.prio < (double)o.prio) record_fill(s, ev, std::min<int32_t>(qty, s.size - s.filled));
                    else s.ahead = std::max<int64_t>(0, s.ahead - qty);
                }
            }
        }
        // better-priced shadows on the same side: bids above o.price, asks below
        std::vector<int32_t> hit;
        if (o.side == 1) { for (auto jt = lm.upper_bound(o.price); jt != lm.end(); ++jt) hit.insert(hit.end(), jt->second.begin(), jt->second.end()); }
        else { for (auto jt = lm.begin(); jt != lm.end() && jt->first < o.price; ++jt) hit.insert(hit.end(), jt->second.begin(), jt->second.end()); }
        for (int32_t id : hit) { Shadow& s = shadows[id]; if (s.alive) record_fill(s, ev, std::min<int32_t>(qty, s.size - s.filled)); }
    }
    void on_remove_ahead(const Order& o, int32_t qty) {   // cancelled / re-queued / reduced volume at o's level
        auto& lm = live(o.side); auto it = lm.find(o.price); if (it == lm.end()) return;
        for (int32_t id : it->second) { Shadow& s = shadows[id]; if (s.alive && s.rule != 1 && s.prio > (double)o.prio) s.ahead = std::max<int64_t>(0, s.ahead - qty); }
    }
    // -- market maker: one lot each side at the touch, joined at the back; re-quoted when the touch moves; inventory beyond the limit is hedged at the opposite touch
    void mm_step(int64_t ts, int64_t ev) {
        if (!mm_on) return;
        int64_t bp, bt, apx, at; bool hb = book.best(1, bp, bt), ha = book.best(-1, apx, at);
        if (!hb || !ha) return;
        // harvest fills of our two shadows
        for (int k = 0; k < 2; ++k) {
            int32_t& ref = k == 0 ? mm_bid : mm_ask; if (ref < 0) continue; Shadow& s = shadows[ref];
            if (s.filled > 0 && s.filled >= s.size) {
                mm_t.push_back(ts); mm_side.push_back(s.side); mm_price.push_back(s.price); mm_qty.push_back(s.filled); mm_kind.push_back(0);
                mm_inv += s.side == 1 ? s.filled : -s.filled; ref = -1;
            } else if (!s.alive) ref = -1;
        }
        // hedge when the inventory limit is breached: cross the spread at the touch
        if (mm_inv >= mm_limit || mm_inv <= -mm_limit) {
            int8_t side = mm_inv > 0 ? -1 : 1; int64_t px = side == 1 ? apx : bp; int32_t q = std::abs(mm_inv);
            mm_t.push_back(ts); mm_side.push_back(side); mm_price.push_back(px); mm_qty.push_back(q); mm_kind.push_back(1); mm_inv = 0;
        }
        // re-quote: cancel a quote that is no longer at the touch; post where we have none (skip the side that would add to inventory beyond half the limit)
        for (int k = 0; k < 2; ++k) {
            int8_t side = k == 0 ? 1 : -1; int32_t& ref = k == 0 ? mm_bid : mm_ask; int64_t touch = side == 1 ? bp : apx;
            if (ref >= 0 && shadows[ref].price != touch) { kill(shadows[ref]); ref = -1; }
            if (ref < 0 && ts >= mm_requote_after) {
                auto& m = book.side_map(side); auto lit = m.find(touch); static Level empty; const Level& lv = lit == m.end() ? empty : lit->second;
                ref = insert_shadow(side, touch, lv, ShadowSpec{2, 0, (int32_t)mm_size}, ts, mm_hold_ns, false);
            }
        }
        (void)ev;
    }
};

py::dict run_shadows(py::array_t<int64_t> ts_a, py::array_t<int8_t> action, py::array_t<int8_t> side, py::array_t<int64_t> price, py::array_t<int32_t> size,
                     py::array_t<int64_t> order_id, py::array_t<int16_t> flags, std::vector<std::tuple<int, int, int>> specs, int64_t insert_every_ns, int64_t horizon_ns,
                     int64_t start_ns, int64_t end_ns, int prorata_min, int top_max, bool real_orders, bool mm, int mm_limit, int mm_size, int64_t mm_hold_ns) {
    auto ts = ts_a.unchecked<1>(); auto ac = action.unchecked<1>(); auto sd = side.unchecked<1>(); auto px = price.unchecked<1>(); auto sz = size.unchecked<1>();
    auto oid = order_id.unchecked<1>(); auto fl = flags.unchecked<1>();
    ssize_t n = ac.shape(0);
    Replay R; R.mm_on = mm; R.mm_limit = mm_limit; R.mm_size = mm_size; R.mm_hold_ns = mm_hold_ns > 0 ? mm_hold_ns : (int64_t)4e12;
    std::vector<ShadowSpec> S; for (auto& t : specs) S.push_back({(int8_t)std::get<0>(t), (int8_t)std::get<1>(t), (int32_t)std::get<2>(t)});
    int64_t next_insert = start_ns; bool alt = false; Order o;
    std::vector<int64_t> ins_ts; std::vector<int8_t> ins_side;
    for (ssize_t i = 0; i < n; ++i) {
        int64_t t = ts(i);
        // expiries
        while (!R.by_expiry.empty() && R.shadows[R.by_expiry.front()].expire <= t) { Shadow& s = R.shadows[R.by_expiry.front()]; R.by_expiry.pop_front(); if (s.alive) R.kill(s); }
        // scheduled insertions (alternating sides), only while the book has both sides
        while (insert_every_ns > 0 && !S.empty() && next_insert <= t && next_insert <= end_ns) {
            int8_t s_side = alt ? -1 : 1; alt = !alt;
            int64_t bp, bt; if (R.book.best(s_side, bp, bt)) {
                const Level& lv = R.book.side_map(s_side)[bp];
                for (auto& sp : S) R.insert_shadow(s_side, bp, lv, sp, next_insert, horizon_ns);
                ins_ts.push_back(next_insert); ins_side.push_back(s_side);
            }
            next_insert += insert_every_ns;
        }
        switch (ac(i)) {
            case ADD: {
                R.book.add(oid(i), sd(i), px(i), sz(i));
                if (real_orders && t >= start_ns && t <= end_ns) { int64_t bp, bt; if (R.book.best(sd(i), bp, bt) && bp == px(i)) R.touch_orders[oid(i)] = {t, bt - sz(i), sd(i), px(i), sz(i)}; }
                break; }
            case CANCEL: {
                if (R.book.cancel(oid(i), o)) { R.on_remove_ahead(o, o.size);
                    auto it = R.touch_orders.find(oid(i)); if (it != R.touch_orders.end()) { R.ro_t_add.push_back(it->second.t_add); R.ro_ahead.push_back(it->second.ahead); R.ro_t_end.push_back(t); R.ro_price.push_back(it->second.price); R.ro_size.push_back(it->second.size); R.ro_side.push_back(it->second.side); R.ro_outcome.push_back(0); R.touch_orders.erase(it); } }
                break; }
            case MODIFY: {
                int r = R.book.modify(oid(i), sd(i), px(i), sz(i), o);
                if (r == 1) R.on_remove_ahead(o, o.size - sz(i)); else if (r == 2) { R.on_remove_ahead(o, o.size); R.touch_orders.erase(oid(i)); }
                break; }
            case FILL: {
                if (R.book.fill(oid(i), sz(i), o)) { R.on_fill(o, sz(i), i);
                    auto it = R.touch_orders.find(oid(i)); if (it != R.touch_orders.end()) { R.ro_t_add.push_back(it->second.t_add); R.ro_ahead.push_back(it->second.ahead); R.ro_t_end.push_back(t); R.ro_price.push_back(it->second.price); R.ro_size.push_back(it->second.size); R.ro_side.push_back(it->second.side); R.ro_outcome.push_back(1); R.touch_orders.erase(it); } }
                break; }
            case TRADE: R.on_trade(sd(i), px(i), sz(i), i, prorata_min, top_max); break;
            case CLEAR: R.book.clear(); R.touch_orders.clear(); break;
            default: break;
        }
        if (fl(i) & 128) R.mm_step(t, i);
    }
    for (auto& kv : R.touch_orders) { R.ro_t_add.push_back(kv.second.t_add); R.ro_ahead.push_back(kv.second.ahead); R.ro_t_end.push_back(ts(n - 1)); R.ro_price.push_back(kv.second.price); R.ro_size.push_back(kv.second.size); R.ro_side.push_back(kv.second.side); R.ro_outcome.push_back(2); }
    auto vec_i64 = [](const std::vector<int64_t>& v) { py::array_t<int64_t> a((ssize_t)v.size()); std::copy(v.begin(), v.end(), a.mutable_data()); return a; };
    auto vec_i32 = [](const std::vector<int32_t>& v) { py::array_t<int32_t> a((ssize_t)v.size()); std::copy(v.begin(), v.end(), a.mutable_data()); return a; };
    auto vec_i8 = [](const std::vector<int8_t>& v) { py::array_t<int8_t> a((ssize_t)v.size()); std::copy(v.begin(), v.end(), a.mutable_data()); return a; };
    auto vec_f = [](const std::vector<double>& v) { py::array_t<double> a((ssize_t)v.size()); std::copy(v.begin(), v.end(), a.mutable_data()); return a; };
    std::vector<int64_t> s_price, s_t, s_ahead, s_q; std::vector<int32_t> s_size, s_filled, s_id; std::vector<int8_t> s_side, s_rule, s_variant; std::vector<double> s_prio;
    for (auto& s : R.shadows) { s_id.push_back(s.id); s_price.push_back(s.price); s_t.push_back(s.t_insert); s_ahead.push_back(s.ahead); s_q.push_back(s.queue_at_insert); s_size.push_back(s.size); s_filled.push_back(s.filled); s_side.push_back(s.side); s_rule.push_back(s.rule); s_variant.push_back(s.variant); s_prio.push_back(s.prio); }
    std::vector<int32_t> f_id, f_qty; std::vector<int64_t> f_ev, f_price;
    for (auto& f : R.fills) { f_id.push_back(f.id); f_ev.push_back(f.event); f_qty.push_back(f.qty); f_price.push_back(f.price); }
    py::dict out;
    py::dict sh; sh["id"] = vec_i32(s_id); sh["side"] = vec_i8(s_side); sh["price"] = vec_i64(s_price); sh["t_insert"] = vec_i64(s_t); sh["queue_at_insert"] = vec_i64(s_q); sh["size"] = vec_i32(s_size); sh["filled"] = vec_i32(s_filled); sh["rule"] = vec_i8(s_rule); sh["variant"] = vec_i8(s_variant); sh["prio"] = vec_f(s_prio);
    py::dict fi; fi["id"] = vec_i32(f_id); fi["event"] = vec_i64(f_ev); fi["qty"] = vec_i32(f_qty); fi["price"] = vec_i64(f_price);
    py::dict ro; ro["t_add"] = vec_i64(R.ro_t_add); ro["ahead"] = vec_i64(R.ro_ahead); ro["t_end"] = vec_i64(R.ro_t_end); ro["price"] = vec_i64(R.ro_price); ro["size"] = vec_i32(R.ro_size); ro["side"] = vec_i8(R.ro_side); ro["outcome"] = vec_i32(R.ro_outcome);
    py::dict mmd; mmd["t"] = vec_i64(R.mm_t); mmd["side"] = vec_i8(R.mm_side); mmd["price"] = vec_i64(R.mm_price); mmd["qty"] = vec_i32(R.mm_qty); mmd["kind"] = vec_i8(R.mm_kind);
    out["shadows"] = sh; out["fills"] = fi; out["real_orders"] = ro; out["mm"] = mmd; out["n_insertions"] = (int64_t)ins_ts.size();
    return out;
}

PYBIND11_MODULE(_replay, m) {
    m.doc() = "MBO book replay: top-of-book arrays, shadow-order queue study, real-order outcomes, passive market maker";
    m.def("replay_top", &replay_top, py::arg("action"), py::arg("side"), py::arg("price"), py::arg("size"), py::arg("order_id"), py::arg("flags"), py::arg("levels") = 10, py::arg("at_flags_last") = true);
    m.def("run_shadows", &run_shadows, py::arg("ts"), py::arg("action"), py::arg("side"), py::arg("price"), py::arg("size"), py::arg("order_id"), py::arg("flags"), py::arg("specs"),
          py::arg("insert_every_ns"), py::arg("horizon_ns"), py::arg("start_ns"), py::arg("end_ns"), py::arg("prorata_min") = 2, py::arg("top_max") = 1000000,
          py::arg("real_orders") = true, py::arg("mm") = false, py::arg("mm_limit") = 5, py::arg("mm_size") = 1, py::arg("mm_hold_ns") = 0);
}
