#!/usr/bin/env python3
"""
breadth_rr_unseen.py — the one breadth version that held on Nifty, on the three years it was never chosen on
================================================================================
breadth_rr_study.py (27 Oct 2026) tried six breadth-only versions on three indices over Sep 2023 - Sep 2026: all lost
pooled, but on Nifty "T70 2.0R" - the day's first 15-minute close with 70% of Nifty's stocks up (buy CE) or down (buy
PE), stop 1 ATR, target 2 x the risk - made money in both periods on its own (+69,007 / +31,565 per lot) and as an
addition to today's system where it holds nothing (+11,239 / +28,882, FRAGILE). Picked from eighteen tries, so it could
be luck. The user, 27 Oct 2026: "yes test it on the 2020 to 2023 data".

DATA: Nifty's 15-minute candles and daily India VIX from 3 Aug 2020 (big_candle_6y_study's, from Zerodha, saved under a
scratch HOME as the 3-year cache names), and the 15-minute candles of Nifty's 49 stocks over the same six years, fetched
the same way into $HOME/trading-tool-logs/history/stocks - today's member lists (survivorship; stocks listed later have
no candles before they listed, and a candle with no stock data is simply fewer stocks). Exactly breadth_rr_study.run and
today's system (order_block_study.rules_rows + the big candle, order_block_site_study.slot), unchanged.

THE UNSEEN YEARS: 3 Aug 2020 - 17 Sep 2023, everything before the three years the version was chosen on.
FIXED BEFORE ANY RESULT WAS SEEN (27 Oct 2026): T70 2.0R is read as real only if, on the unseen years, BOTH
  on its own it makes money after costs (profit factor above 1), and
  added to today's system where it holds nothing, it makes today's system more.
Otherwise the three-year result was luck. The other five versions are printed beside it for context only - this is a
check of one version, not a new search. The three chosen-on years are re-run from the six-year data as a check that it
reproduces breadth_rr_study's figures (to 11 Sep 2026, where the three-year cache ends).

RESULT (28 Oct 2026; breadth_rr_unseen_results.txt) - DOES NOT HOLD: THE THREE-YEAR RESULT WAS LUCK.
  T70 2.0R on the unseen years (3 Aug 2020 - 17 Sep 2023), per lot: on its own 616 trades, 33% won, -57,323, PF 0.89,
  losing in every calendar year (2020 -16,719, 2021 -6,715, 2022 -9,645, 2023 -24,244); added to today's system it takes
  away -33,733 (today's +141,816 there, PF 1.21). The other five versions lose on their own too (-39k to -65k, PF
  0.86-0.91) and every one takes away from today's (-34k to -59k). The check reproduces breadth_rr_study's chosen-on
  years (T70 2.0R +68,996 / +31,160 against +69,007 / +31,565; today's differs by a few thousand from the longer warm-up).
  The 49 stocks' six-year candles equal the three-year files on every candle both have. Known limit: a stock split or
  bonus in 2020-23 reads as one day's fall for that stock (Kite's candles are not adjusted) - one day each, both sides.

    HOME=<scratch with the 6-year history and Nifty's stocks> /usr/bin/python3 breadth_rr_unseen.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

KEY = "NIFTY"
CHECKED = "T70 2.0R"
UNSEEN = ("2020-08-01", "2023-09-18")             # [from, to): before the three years breadth_rr_study was run on
CHOSEN = ("2023-09-18", "2026-09-12")
SPLIT = "2025-08-15"


def window(rows, a, b):
    lo, hi = pd.Timestamp(a, tz="Asia/Kolkata"), pd.Timestamp(b, tz="Asia/Kolkata")
    return [r for r in rows if lo <= pd.Timestamp(r["when"]) < hi]


def summary(rows):
    """trades, won, total, profit factor, worst drop - per lot after costs."""
    if not rows:
        return "no trades"
    net = np.array([r["net"] for r in sorted(rows, key=lambda r: pd.Timestamp(r["when"]))], float)
    eq = np.cumsum(net)
    dd = float(np.max(np.maximum.accumulate(np.r_[0.0, eq]) - np.r_[0.0, eq]))
    pf = net[net > 0].sum() / max(1e-9, -net[net < 0].sum())
    return f"{len(net):>5} trades  won {np.mean(net > 0):>4.0%}  total ₹{net.sum():>+10,.0f}  PF {pf:4.2f}  worst drop ₹{dd:>8,.0f}"


def by_year(rows):
    s = pd.Series({}, dtype=float)
    for r in rows:
        y = pd.Timestamp(r["when"]).year
        s[y] = s.get(y, 0.0) + r["net"]
    return "  ".join(f"{y}: {v:+,.0f}" for y, v in sorted(s.items()))


def _job(what):
    if what == "breadth":
        import breadth_rr_study as brs
        return what, brs.run(KEY)
    if what == "rules":
        import order_block_study as ob
        return what, ob.rules_rows(KEY)
    import big_candle_study as bcs
    return what, bcs.run(KEY, ("K1.5 end 2R", 1.5, True, "end", "2R"))


def main():
    import multiprocessing as mp
    import breadth_rr_study as brs
    import order_block_site_study as obs
    with mp.get_context("spawn").Pool(processes=3, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, ["breadth", "rules", "bc"], chunksize=1))
    s_ = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    rules, bc, br = s_(got["rules"]), s_(got["bc"]), got["breadth"]
    today = obs.slot(rules, bc)
    plus = {v: obs.slot(rules, s_(bc + br[v])) for v in brs.VARIANTS}
    W = 150
    print("=" * W)
    print(f" NIFTY BREADTH ALONE ON THE YEARS IT WAS NEVER CHOSEN ON ({UNSEEN[0]} to {UNSEEN[1]}) - per lot, after costs")
    print("=" * W)
    u = lambda rows: window(rows, *UNSEEN)
    print(" ON ITS OWN")
    for v in brs.VARIANTS:
        print(f"   {v:10s}{'  <- the one checked' if v == CHECKED else '':22s} {summary(u(br[v]))}")
        if v == CHECKED:
            print(f"   {'':32s} by year  {by_year(u(br[v]))}")
    print("\n AS AN ADDITION to today's system (the rules + the big candle), where it holds nothing")
    print(f"   {'today':32s} {summary(u(today))}")
    print(f"   {'':32s} by year  {by_year(u(today))}")
    for v in brs.VARIANTS:
        add = sum(r["net"] for r in u(plus[v])) - sum(r["net"] for r in u(today))
        print(f"   + {v:10s}{'  <- the one checked' if v == CHECKED else '':18s} {summary(u(plus[v]))}   adds ₹{add:+,.0f}")
    alone_ok = (lambda rows: bool(rows) and sum(r["net"] for r in rows if r["net"] > 0)
                > -sum(r["net"] for r in rows if r["net"] < 0))(u(br[CHECKED]))
    adds = sum(r["net"] for r in u(plus[CHECKED])) - sum(r["net"] for r in u(today))
    print(f"\n VERDICT for {CHECKED}: on its own {'PF above 1' if alone_ok else 'PF 1 or below'}; as an addition "
          f"{'adds' if adds > 0 else 'takes away'} ₹{abs(adds):,.0f}  ->  "
          f"{'HOLDS on the unseen years' if alone_ok and adds > 0 else 'DOES NOT HOLD - the three-year result was luck'}")
    print("\n CHECK: the three chosen-on years from the six-year data (breadth_rr_study's figures for Nifty: T70 2.0R on its"
          " own +69,007 / +31,565; today's +190,286 / +66,078)")
    for lab, a, b in (("first two years", CHOSEN[0], SPLIT), ("held-out", SPLIT, CHOSEN[1])):
        print(f"   {lab:16s} {CHECKED}: {summary(window(br[CHECKED], a, b))}")
        print(f"   {'':16s} today's: {summary(window(today, a, b))}")
    return got


if __name__ == "__main__":
    main()
