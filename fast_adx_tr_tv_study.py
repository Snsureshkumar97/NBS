#!/usr/bin/env python3
"""
fast_adx_tr_tv_study.py — the fast ADX (DX over 3) for the Trend Rider and the TradingView rating too (3 years)
================================================================================
The user, 14 Oct 2026, after the rules' ADX went back to the fast one: "yes make fast adx for that as well" - the
Trend Rider and the TradingView rating, which were built and measured with the standard ADX(14).

FIXED BEFORE ANY RESULT WAS SEEN (14 Oct 2026), Nifty + Bank Nifty, each system with its live exit (the Trend Rider's
rules exit: the stop to T1 / T2 at the candle close, the Supertrend trail, the 2-hour breakeven; 2.75R target),
5-minute walk, at the money on the real expiry, 0.25% slippage, every charge:
  TREND RIDER   entry "ADX over 20 and rising" with the standard ADX(14) (today, asserted) vs DX over 3
  TV RATING     the overall rating with its ADX vote on ADX(14, 14) (today, asserted) vs DX over 3
A fast version is better only if it makes more in BOTH the first two years and the held-out year, the two pooled.

RESULT (14 Oct 2026, history to 11 Sep 2026; fast_adx_tr_tv_study_results.txt) - BOTH DROP, nothing switched. Nifty +
Bank Nifty, per lot, first two years / held-out:
  Trend Rider       standard +275,631 / +121,022, fast +329,288 / +102,971 (+53,657 / -18,051): Nifty far worse held-out
                    (+40,583 against +70,163), Bank Nifty better in both (+165,483 / +62,387 against +118,299 / +50,859)
  TradingView rating standard +448,955 / +219,244, fast +446,461 / +173,594 (-2,493 / -45,651): worse on Nifty in both
The user: "leave the fast adx for trendrider and trading view settings" - both stay on the standard ADX. The dx_length
hooks this ran with (pasted_combos_study.signals, tv_rating.ratings: ADX averaged over dx_length) were taken back out,
so re-running this file needs them added again. A record, not a runnable check.

    python3 fast_adx_tr_tv_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TREND = ("NIFTY", "BANKNIFTY")
TR_FIGS = {"NIFTY": (157331, 70163), "BANKNIFTY": (118299, 50859)}
TV_FIGS = {"NIFTY": (267670, 143011), "BANKNIFTY": (181285, 76234)}


def _job(job):
    k, system, speed = job
    import functools
    if speed == "FAST":
        import pasted_combos_study as pc
        import tv_rating
        pc.signals = functools.partial(pc.signals, dx_length=3)
        tv_rating.ratings = functools.partial(tv_rating.ratings, dx_length=3)
    import tv_ratings_study as tvs
    _, out = tvs._tv_job(k)
    return job, out["tool" if system == "TR" else "tv_ours"]


def main():
    import multiprocessing as mp
    import stop_day_study as sds
    jobs = [(k, s, v) for k in TREND for s in ("TR", "TV") for v in ("STANDARD", "FAST")]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    for k in TREND:
        for s, figs in (("TR", TR_FIGS), ("TV", TV_FIGS)):
            r = sds.split(got[(k, s, "STANDARD")])
            assert (round(r["is"]["total"]), round(r["oos"]["total"])) == figs[k], f"{k} {s}: standard must be today's"
    pool_ = lambda s, v: sorted([r for k in TREND for r in got[(k, s, v)]], key=lambda r: r["when"])
    W = 172
    print("=" * W)
    print(" FAST ADX (DX over 3) FOR THE TREND RIDER AND THE TRADINGVIEW RATING - Nifty + Bank Nifty, 5-minute walk, live exits."
          " Per lot, after costs.  columns: trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    for s, name in (("TR", "TREND RIDER"), ("TV", "TRADINGVIEW RATING")):
        print(f" {name}, Nifty + Bank Nifty")
        for v in ("STANDARD", "FAST"):
            print(sds.line(f"   {v} ADX", pool_(s, v)))
        for k in TREND:
            for v in ("STANDARD", "FAST"):
                print(sds.line(f"     {k} {v}", got[(k, s, v)]))
    print("\n VERDICT - the fast ADX kept only if more than the standard in BOTH periods (Nifty + Bank Nifty)")
    for s, name in (("TR", "Trend Rider"), ("TV", "TradingView rating")):
        print("  " + sds.verdict(f"{name}: fast ADX", pool_(s, "FAST"), pool_(s, "STANDARD")).strip())
    return got


if __name__ == "__main__":
    main()
