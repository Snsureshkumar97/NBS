#!/usr/bin/env python3
"""
next_day_range_study.py — a calibrated range for the day (high and low inside it ~80% of days): accurate, and better room?
================================================================================
The user, 19-20 Oct 2026, after a product page for a paid Windows app: "can you go through this link and see anyfeature
is usefull", then "yes run both tests" (this one and order_block_study.py). The app's one claim with evidence: a range
for the next day sized so the day's high and low both stayed inside it on ~80% of past days. The tool's room to run
(signal_engine.compute_reachability) is the closest thing it has; live it over-states the move (room_to_run_accuracy_
study: about 2x what is really travelled) - and the user worries about targets that are too far to be reached.

FIXED BEFORE ANY RESULT WAS SEEN (20 Oct 2026):
  THE BAND  for day d: the previous day's close +/- k x the daily ATR(14) as of the previous day (Wilder, the gap
            counted) - known before the open. k is the smallest (in steps of 0.01) that kept BOTH the day's high and
            low inside the band on at least 80% of days in the FIRST TWO YEARS (to 15 Aug 2025); the held-out year is
            then measured with that k untouched. Per instrument.
  COMPARED WITH a plain band: the previous close +/- m x the mean range of the last 10 days (the tool's own "normal
            day"), m calibrated the same way. At the same 80%, the narrower band is the more useful one.
  ACCURACY  Nifty, Bank Nifty, Sensex, Midcap (the session's candles), Bitcoin and gold (Exness candles, IST days; a
            day with under half the usual candles - a weekend stub - is left out).
  TRADING   the Indian rules as live runs them today (order_block_study.rules_rows: the room as live gives it since 15 Oct,
            out at T3 with the stop to T1 / T2, the Supertrend trail, the 2-hour breakeven, the T1-exit re-entry, every
            charge, per lot; Sensex and Midcap asserted), with the room to run changed:
              BANDCAP  the room each way is also capped at the band's edge (a 4th limit; at or past the edge, no room)
              BAND     the room each way IS the distance to the band's edge (in place of the expected move and the
                       day's range left)
            Kept only if more than today's in BOTH periods, Nifty + Sensex + Midcap pooled, and on each index too.
            Bank Nifty runs the TradingView rating (its own targets, no room) and crypto's targets are set by ATR, so
            neither is traded here.

RESULT (20 Oct 2026, Indian history to 11 Sep 2026, Exness candles to Sep 2026; next_day_range_study_results.txt) -
NOTHING BUILT.
  ACCURATE BUT WIDE. k = 1.10-1.16 x ATR everywhere; held-out 77.7-83.0% of days inside (Nifty 79.2%, Bank Nifty 78.9%,
  Sensex 78.9%, Midcap 77.7%, BTC 83.0%, gold 80.4%) - the app's ~80% claim holds. But to hold both ends on 80% of days
  the band is 2.6-3.1x the day's real range, and the plain 10-day band does as well (77.0-81.6%, as wide).
  AS ROOM, Nifty + Sensex + Midcap against today's live room, first two years / held-out:
    BANDCAP  -78,628 / +5,384    DROP (Midcap worse in both)
    BAND    +53,705 / +114,290   passes pooled, Nifty and Sensex; Midcap -73,024 / +8,139. Not new: it wins by dropping
            the expected-move cap, as room_fix_study's C did (14 Oct) - and C (the day's range alone, config "range",
            built then rolled back by the user on 15 Oct for its far targets) made more than BAND: Nifty + Sensex + Midcap
            +658,593 / +277,239 against BAND's +402,151 / +308,618. BAND's targets are FARTHER still (won 42% against
            51% today) - the opposite of what the user asked for on 15 Oct.
  T3 IS RARELY THE EXIT in any version: 2.3-3.6% of trades close at the target; ~77-80% on the trail, the breakeven
  or the close, ~17-21% at the stop.

    /usr/bin/python3 next_day_range_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
COVER = 0.80
RULES = ("NIFTY", "SENSEX", "MIDCPNIFTY")
INDIAN = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
CRYPTO = {"BTCUSD": "BTC", "XAUUSD": "GOLD"}
MODES = ("BASE", "BANDCAP", "BAND")


def daily(df15):
    """Daily high / low / close from 15-minute candles, by IST date. Short days (under half the usual candles) out."""
    import pandas as pd
    idx = df15.index.tz_convert("Asia/Kolkata") if df15.index.tz is not None else df15.index
    day = pd.Series([t.date() for t in idx], index=df15.index)
    g = df15.groupby(day.to_numpy())
    d = pd.DataFrame({"h": g["High"].max(), "l": g["Low"].min(), "c": g["Close"].last(), "n": g["Close"].size()})
    return d[d["n"] >= 0.5 * d["n"].median()]


def bands(d):
    """For each day: (previous close, previous ATR(14), previous 10-day mean range) - all known before the open."""
    import numpy as np
    pc = d["c"].shift(1)
    tr = np.maximum(d["h"] - d["l"], np.maximum((d["h"] - pc).abs(), (d["l"] - pc).abs()))
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    r10 = (d["h"] - d["l"]).rolling(10).mean()
    return {"pc": pc, "atr": atr.shift(1), "r10": r10.shift(1)}


def inside(d, B, unit, k):
    """Per day: True when the day's high AND low were inside previous close +/- k x unit."""
    return (d["h"] <= B["pc"] + k * B[unit]) & (d["l"] >= B["pc"] - k * B[unit])


def calibrate(d, B, unit, split_date):
    """The smallest k with in-sample coverage >= COVER, and its coverage in each period."""
    import numpy as np
    ok = B["pc"].notna() & B[unit].notna()
    dd = d[ok]
    is_ = np.array([x < split_date for x in dd.index])
    for k in np.arange(0.25, 8.0, 0.01):
        hit = inside(dd, {kk: v[ok] for kk, v in B.items()}, unit, k).to_numpy()
        if hit[is_].mean() >= COVER:
            k = round(float(k), 2)
            w = (2 * k * B[unit][ok]).to_numpy()
            rng = (dd["h"] - dd["l"]).to_numpy()
            top = (B["pc"][ok] + k * B[unit][ok]).to_numpy()
            bot = (B["pc"][ok] - k * B[unit][ok]).to_numpy()
            return {"k": k, "is": hit[is_].mean(), "oos": hit[~is_].mean(), "n_is": int(is_.sum()), "n_oos": int((~is_).sum()),
                    "width_is": float(np.mean(w[is_] / rng[is_])), "width_oos": float(np.mean(w[~is_] / rng[~is_])),
                    "hi_out_oos": float((dd["h"].to_numpy()[~is_] > top[~is_]).mean()),
                    "lo_out_oos": float((dd["l"].to_numpy()[~is_] < bot[~is_]).mean())}
    return None


def _hook(mode, k):
    """The room after live_room_study's B, capped at (BANDCAP) or replaced by (BAND) the band's edge."""
    def hook(key):
        import pandas as pd
        import backtest_intraday as bt
        import signal_engine as se
        d = daily(bt.fetch_history(key, years=3, use_cache=True))
        B = bands(d)
        edge = {day: (B["pc"][day] - k * B["atr"][day], B["pc"][day] + k * B["atr"][day])
                for day in d.index if B["pc"][day] == B["pc"][day] and B["atr"][day] == B["atr"][day]}
        inner = se.compute_reachability

        def banded(spot, oi, df_, now, *a, **kw):
            out = inner(spot, oi, df_, now, *a, **kw)
            e = edge.get(pd.Timestamp(now).date())
            if e is None:
                return out
            up, dn = e[1] - spot, spot - e[0]
            up, dn = (up if up > 0 else 0.01), (dn if dn > 0 else 0.01)      # at or past the edge: no room that way
            for side, room in (("up", up), ("down", dn)):
                cur = out.get(f"reach_{side}")
                if mode == "BAND" or cur is None or room < cur:
                    out[f"reach_{side}"], out[f"cap_{side}"] = round(room, 2), "the day's calibrated range"
            out["available"] = True
            return out
        se.compute_reachability = banded
    return hook


def _job(job):
    key, mode, k = job
    import order_block_study as ob
    return job[:2], ob.rules_rows(key, room_hook=None if mode == "BASE" else _hook(mode, k))


def main():
    import multiprocessing as mp
    import pandas as pd
    import backtest_intraday as bt
    import exness_data as ed
    import regime_study as rs
    import stop_day_study as sds
    import order_block_study as ob
    split_date = rs.SPLIT.date()
    W = 160
    print("=" * W)
    print(" A RANGE FOR THE DAY - previous close +/- k x daily ATR(14), k set so 80% of days in the first two years had their"
          " high AND low inside; the held-out year measured with k untouched")
    print("=" * W)
    print(f" {'':12s} {'band':26s} {'k':>5s}  {'inside: first two yrs':>22s}  {'HELD-OUT':>9s}  {'days':>9s}"
          f"  {'width / day range':>18s}  {'held-out: high out / low out':>28s}")
    K = {}
    sources = [(k, bt.fetch_history(k, years=3, use_cache=True)) for k in INDIAN]
    sources += [(CRYPTO[s], ed.load(s)[["Open", "High", "Low", "Close"]]) for s in CRYPTO]
    for name, df in sources:
        d = daily(df)
        B = bands(d)
        for unit, lab in (("atr", "close +/- k x ATR(14)"), ("r10", "close +/- m x 10-day range")):
            c = calibrate(d, B, unit, split_date)
            if c is None:
                print(f" {name:12s} {lab:26s}  no k up to 8 reaches 80%")
                continue
            if unit == "atr":
                K[name] = c["k"]
            print(f" {name:12s} {lab:26s} {c['k']:>5.2f}  {c['is']:>21.1%}  {c['oos']:>9.1%}  {c['n_is']:>4}/{c['n_oos']:<4}"
                  f"  {c['width_is']:>7.2f}x / {c['width_oos']:.2f}x  {c['hi_out_oos']:>13.1%} / {c['lo_out_oos']:.1%}")

    jobs = [(k, m, K[k]) for k in RULES for m in MODES]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    for k, figs in ob.B_FIGS.items():
        s = sds.split(got[(k, "BASE")])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == figs, f"{k} BASE must be room_fix_study's B {figs}"
    pool_ = lambda m: sorted([r for k in RULES for r in got[(k, m)]], key=lambda r: r["when"])
    print("\n" + "=" * W)
    print(" THE BAND AS ROOM TO RUN on the Indian rules as live runs them (per lot after costs, ₹; columns: trades, won, total,"
          " profit factor, worst drop, worst day)")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    label = {"BASE": "TODAY (live room)", "BANDCAP": "BANDCAP (also capped at the band)", "BAND": "BAND (the band is the room)"}
    print(" NIFTY + SENSEX + MIDCAP (the verdict)")
    for m in MODES:
        print(sds.line("   " + label[m], pool_(m)))
    for k in RULES:
        print(f" {k}  (band k = {K[k]:.2f})")
        for m in MODES:
            print(sds.line("   " + label[m], got[(k, m)]))
    print("\n HOW TRADES ENDED (all three indices) - share closed AT THE TARGET, at the stop, and otherwise")
    for m in MODES:
        rs_ = pool_(m)
        for p, sel in (("first two years", [r for r in rs_ if r["when"] < rs.SPLIT]), ("held-out", [r for r in rs_ if r["when"] >= rs.SPLIT])):
            n = len(sel) or 1
            c = pd.Series([r["closed_via"] for r in sel]).value_counts()
            print(f"   {label[m]:36s} {p:16s} target {c.get('target', 0) / n:5.1%}   stop {c.get('stop', 0) / n:5.1%}"
                  f"   other (trail, breakeven, close) {c.get('other', 0) / n:5.1%}   of {len(sel)}")
    print("\n VERDICT - kept only if more than TODAY in BOTH periods (pooled), and then on each index too")
    for m in MODES[1:]:
        print("  " + sds.verdict(f"{m} pooled", pool_(m), pool_("BASE")).strip())
        for k in RULES:
            print("      " + sds.verdict(f"{m} {k}", got[(k, m)], got[(k, "BASE")]).strip())
    return got


if __name__ == "__main__":
    main()
