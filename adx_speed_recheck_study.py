#!/usr/bin/env python3
"""
adx_speed_recheck_study.py — the rules' fast ADX (DX over 3) against the standard ADX(14), re-checked on the rules as
they run from 15 Oct 2026
================================================================================
The user, 14 Oct 2026, after seeing the Signal card's ADX disagree with Kite's: "re-test fast vs standard adx".

Since 17 Sep 2026 (trend_speed_study.py, the user's choice) the Indian rules' ADX keeps +DI / -DI over 14 candles but
averages DX over 3 (config.ADX_DX_SMOOTHING["nse_index"] = 3) - faster than Wilder's ADX(14, 14) that Kite and
TradingView draw. It was chosen on the system of that day; since then the exit moved to T3, the stop steps at the
candle close, a T1 exit may re-enter, and (from 15 Oct) the room to run is the day's range alone. ADX reaches the
entry gate (20), the room's trend stretch, the T1-exit re-entry and the cooldown waiver.

FIXED BEFORE ANY RESULT WAS SEEN (14 Oct 2026), the rules exactly as they run from 15 Oct (room_fix_study's C,
asserted): DX over 3 (today) / over 14 (standard - Kite, TradingView) / over 7 (information). t3_exit_recheck_study's
walk on 15-minute candles, Sensex + Midcap pooled, and Sensex's 5-minute walk (the step at the candle close). The
standard ADX is preferred only if it makes more than today's in BOTH the first two years and the held-out year.

RESULT (14 Oct 2026, history to 11 Sep 2026; adx_speed_recheck_study_results.txt) - THE FAST ADX STAYS. Per lot,
first two years / held-out: Sensex + Midcap, fast 3 +489,948 / +204,688, standard 14 +533,112 / +181,485 (+43,163 /
-23,203: not better in both); Sensex 5-minute fast +199,733 / +63,091, standard +196,186 / +57,347 (worse in both).
(info) DX over 7: +558,239 / +232,657 (+68,290 / +27,969 against fast), Sensex 5-minute +197,827 / +101,849 (-1,906 /
+38,758), Midcap level in the held-out year (-17) - better, but it was the in-between shown for information, chosen
after seeing three; only a pre-declared sweep could say whether 7 is a plateau or a lucky point. Nothing changed live.

    python3 adx_speed_recheck_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RULES = ("SENSEX", "MIDCPNIFTY")
DX = {"FAST 3 (today)": 3, "STANDARD 14 (Kite)": None, "(info) 7": 7}
C_FIGS = {("SENSEX", "15"): (225647, 119188), ("MIDCPNIFTY", "15"): (264301, 85500), ("SENSEX", "5"): (199733, 63091)}


def _job(job):
    key, walk, label = job
    import config
    import live_room_study as lrs
    config.ADX_DX_SMOOTHING = dict(config.ADX_DX_SMOOTHING, nse_index=DX[label])
    lrs.install_live_room(key, chain=False, normal_days=10)       # the room from 15 Oct (room_fix_study's C)
    if walk == "15":
        import t3_exit_recheck_study as t3
        _, out = t3._job(key)
        return job, out["t3re"]
    import t1_step_timing_study as tst
    return job, tst._job((key, "FIX"))[1]


def main():
    import multiprocessing as mp
    import stop_day_study as sds
    jobs = [(k, "15", d) for k in RULES for d in DX] + [("SENSEX", "5", d) for d in DX]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    for (k, w), (a, b) in C_FIGS.items():
        s = sds.split(got[(k, w, "FAST 3 (today)")])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), f"{k} {w}: today must be room_fix_study C"
    pool_ = lambda d: sorted([r for k in RULES for r in got[(k, "15", d)]], key=lambda r: r["when"])
    W = 172
    print("=" * W)
    print(" FAST ADX (DX over 3) vs STANDARD ADX(14) - the rules as they run from 15 Oct 2026. Per lot, after costs.  columns:"
          " trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print(" SENSEX + MIDCAP, 15-minute walk")
    for d in DX:
        print(sds.line("   " + d, pool_(d)))
    print(" SENSEX, 5-minute walk (the step at the candle close)")
    for d in DX:
        print(sds.line("   " + d, got[("SENSEX", "5", d)]))
    for k in RULES:
        print(f" {k}, 15-minute walk")
        for d in DX:
            print(sds.line("   " + d, got[(k, "15", d)]))
    print("\n VERDICT - the standard ADX against today's fast one, preferred only if more in BOTH periods")
    print("  " + sds.verdict("STANDARD 14, Sensex + Midcap", pool_("STANDARD 14 (Kite)"), pool_("FAST 3 (today)")).strip())
    print("  " + sds.verdict("STANDARD 14, Sensex 5-minute", got[("SENSEX", "5", "STANDARD 14 (Kite)")],
                             got[("SENSEX", "5", "FAST 3 (today)")]).strip())
    print("  " + sds.verdict("(info) 7, Sensex + Midcap", pool_("(info) 7"), pool_("FAST 3 (today)")).strip())
    return got


if __name__ == "__main__":
    main()
