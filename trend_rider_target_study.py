#!/usr/bin/env python3
"""
trend_rider_target_study.py — how far the Trend Rider's target should be (Nifty + Bank Nifty, 3 years)
================================================================================
The user, 13 Oct 2026: "why trend rider targets are faraway from the entry", then "yes run the target test".

Today the Trend Rider's one target is 2.75 x the risk (config.TREND_RIDER["target_r"]; the risk is the entry to the
last 5 candles' low/high), its T1 / T2 / T3 a third, two thirds and all of it, and the stop steps up 90% of the way to
T2 (to T1) and 90% of the way to T3 (to T2) - config.NEAR_TARGET_STEPS. FIXED BEFORE ANY RESULT WAS SEEN: the target at
1.5, 2.0, 2.5, 2.75 (today) and 3.0 x the risk, the ladder scaled with it (thirds of whatever the target is), nothing
else changed - trend_rider_t1_study.py's walk on the 5-minute candles (15-minute for information), priced at the money
on the real expiry, 0.25% slippage a side, every charge, per lot; 2.75 asserted to reproduce the live figures. A target
is better than today's only if it makes more in BOTH the first two years and the held-out final year, the two indices
pooled.

RESULT (13 Oct 2026, history to 11 Sep 2026; trend_rider_target_study_results.txt) - 2.75 STAYS. Nifty + Bank Nifty,
5-minute, first two years / held-out: 1.5x +337,810 / +42,896; 2.0x +307,332 / +47,729; 2.5x +361,301 / +90,347;
2.75x (today) +339,081 / +115,033; 3.0x +351,169 / +100,477. Closer targets win more often (46-49% vs 41-47%) and earn
far less - Bank Nifty's held-out year turns to a loss at 1.5x and 2.0x. 2.5x and 3.0x make a little more in the first
two years and less in the held-out one: 2.5-3.0x is a plateau. Honest caveat: 2.75 was itself picked on 7 Oct from a
sweep that saw both periods, so its edge over 2.5 and 3.0 is no stronger evidence than that plateau.

    python3 trend_rider_target_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TREND = ("NIFTY", "BANKNIFTY")
TARGETS = (1.5, 2.0, 2.5, 2.75, 3.0)
TODAY = 2.75
LADDER = "ladder"


def _job(job):
    k, R = job
    import trend_rider_t1_study as trs
    trs.RR = R                                       # set on every job: workers are reused
    trs.VARIANTS = [("no steps (the study's own check)", None, None, False),
                    (LADDER, [(R * 2 / 3 * 0.9, R / 3), (R * 0.9, R * 2 / 3)], "ladder", False)]
    _, out = trs._job(k)
    return job, {"5": out[(LADDER, "5")], "15": out[(LADDER, "15")]}


def main():
    from concurrent.futures import ProcessPoolExecutor
    import stop_day_study as sds
    with ProcessPoolExecutor(max_workers=5) as ex:
        got = dict(ex.map(_job, [(k, R) for k in TREND for R in TARGETS]))
    for k, (a, b) in {"NIFTY": (179191, 81254), "BANKNIFTY": (159890, 33779)}.items():
        s = sds.split(got[(k, TODAY)]["5"])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), f"{k}: 2.75 must be the live figures"
    pool = lambda R, res: sorted([r for k in TREND for r in got[(k, R)][res]], key=lambda r: r["when"])
    W = 172
    print("=" * W)
    print(" THE TREND RIDER'S TARGET - how many times the risk, the near-target ladder scaled with it. Per lot, after costs."
          "  columns: trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    for res, title in (("5", "NIFTY + BANK NIFTY, 5-minute candles"), ("15", "NIFTY + BANK NIFTY, 15-minute candles (info)")):
        print(" " + title)
        for R in TARGETS:
            print(sds.line(f"   target {R:g} x risk{' (today)' if R == TODAY else ''}", pool(R, res)))
    for k in TREND:
        print(f" {k}, 5-minute candles")
        for R in TARGETS:
            print(sds.line(f"   target {R:g} x risk{' (today)' if R == TODAY else ''}", got[(k, R)]["5"]))
    print("\n VERDICT - better than today's 2.75 only if more profit in BOTH periods (5-minute, the two indices pooled)")
    for R in TARGETS:
        if R != TODAY:
            print("  " + sds.verdict(f"target {R:g} x risk", pool(R, "5"), pool(TODAY, "5")).strip())
    return got


if __name__ == "__main__":
    main()
