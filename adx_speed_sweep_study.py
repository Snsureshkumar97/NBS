#!/usr/bin/env python3
"""
adx_speed_sweep_study.py — is DX-over-7 a real plateau or a lucky point? A pre-declared sweep of the rules' ADX speed
================================================================================
The user, 14 Oct 2026, after adx_speed_recheck_study.py (fast 3 stays over the standard 14; 7, shown for information,
looked better): "yes run the sweep".

FIXED BEFORE ANY RESULT WAS SEEN (14 Oct 2026): the rules exactly as they run from 15 Oct (room_fix_study's C, asserted
at 3), DX averaged over 3 (today), 4, 5, 6, 7, 8, 10, 12, 14 candles (+DI / -DI over 14 throughout). Sensex + Midcap on
15-minute candles (t3_exit_recheck_study's walk) and Sensex on 5-minute candles (the step at the candle close).
DECISION: switch to 7 ONLY IF 7 and both its neighbours (6 and 8) make more than 3 in BOTH periods on Sensex + Midcap,
AND 7 makes more than 3 in both periods on Sensex's 5-minute walk. Otherwise 3 stays.

RESULT (14 Oct 2026, history to 11 Sep 2026; adx_speed_sweep_study_results.txt). By the pre-declared rule, 3 STAYS:
7 and its neighbours 6 and 8 all beat 3 in both periods on Sensex + Midcap, but 7 fell short on Sensex's 5-minute walk in
the first two years (-1,906; +38,758 held-out). THE WIDER PICTURE: every speed from 4 to 10 beats today's 3 in both
periods on Sensex + Midcap - 3 is the edge of a plateau, not its top. 5 and 6 beat 3 in both periods everywhere:
  DX over 6: Sensex + Midcap +649,069 / +320,345 (+159,120 / +115,657), Sensex 5-minute +210,022 / +109,993 (+10,289 /
  +46,902), Sensex 15-minute +268,564 / +164,616, Midcap +380,504 / +155,730; worst drop 36,928 / 31,595 (3: 38,377 /
  49,424). DX over 5 nearly the same. 12 and 14 (the standard) fall back below 3 in the held-out year.
Choosing 5 or 6 after this run is a choice made on the results; the whole 4-10 band beating 3 is what makes it more than
luck. Nothing changed live - the user's call.

    python3 adx_speed_sweep_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RULES = ("SENSEX", "MIDCPNIFTY")
SPEEDS = (3, 4, 5, 6, 7, 8, 10, 12, 14)
C_FIGS = {("SENSEX", "15"): (225647, 119188), ("MIDCPNIFTY", "15"): (264301, 85500), ("SENSEX", "5"): (199733, 63091)}


def _job(job):
    key, walk, dx = job
    import config
    import live_room_study as lrs
    config.ADX_DX_SMOOTHING = dict(config.ADX_DX_SMOOTHING, nse_index=dx)
    lrs.install_live_room(key, chain=False, normal_days=10)
    if walk == "15":
        import t3_exit_recheck_study as t3
        return job, t3._job(key)[1]["t3re"]
    import t1_step_timing_study as tst
    return job, tst._job((key, "FIX"))[1]


def main():
    import multiprocessing as mp
    import stop_day_study as sds
    jobs = [(k, "15", d) for k in RULES for d in SPEEDS] + [("SENSEX", "5", d) for d in SPEEDS]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    for (k, w), (a, b) in C_FIGS.items():
        s = sds.split(got[(k, w, 3)])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), f"{k} {w}: 3 must be room_fix_study C"
    pool_ = lambda d: sorted([r for k in RULES for r in got[(k, "15", d)]], key=lambda r: r["when"])
    tot = lambda rows: (lambda s: (s["is"]["total"], s["oos"]["total"]))(sds.split(rows))
    W = 172
    print("=" * W)
    print(" THE RULES' ADX SPEED - DX averaged over N candles (+DI/-DI over 14). The rules as they run from 15 Oct 2026."
          " Per lot, after costs.  columns: trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print(" SENSEX + MIDCAP, 15-minute walk")
    for d in SPEEDS:
        print(sds.line(f"   DX over {d}{' (today)' if d == 3 else ' (standard)' if d == 14 else ''}", pool_(d)))
    print(" SENSEX, 5-minute walk")
    for d in SPEEDS:
        print(sds.line(f"   DX over {d}{' (today)' if d == 3 else ''}", got[("SENSEX", "5", d)]))
    for k in RULES:
        print(f" {k}, 15-minute walk")
        for d in SPEEDS:
            print(sds.line(f"   DX over {d}", got[(k, "15", d)]))
    base = tot(pool_(3))
    b5 = tot(got[("SENSEX", "5", 3)])
    beats = {d: all(x > y for x, y in zip(tot(pool_(d)), base)) for d in SPEEDS if d != 3}
    beats5 = all(x > y for x, y in zip(tot(got[("SENSEX", "5", 7)]), b5))
    print("\n AGAINST TODAY'S 3 - more in both periods?")
    for d in SPEEDS[1:]:
        p, q = tot(pool_(d)), tot(got[("SENSEX", "5", d)])
        print(f"   DX over {d:>2}: Sensex + Midcap {p[0] - base[0]:>+9,.0f} / {p[1] - base[1]:>+9,.0f} "
              f"{'BOTH' if beats[d] else '    '}   Sensex 5-minute {q[0] - b5[0]:>+9,.0f} / {q[1] - b5[1]:>+9,.0f}")
    ok = beats[6] and beats[7] and beats[8] and beats5
    print(f"\n DECISION (pre-declared): 7 and its neighbours 6 and 8 beat 3 in both periods: {beats[6] and beats[7] and beats[8]};"
          f" 7 beats 3 in both on Sensex 5-minute: {beats5} -> {'SWITCH TO 7' if ok else '3 STAYS'}")
    return got


if __name__ == "__main__":
    main()
