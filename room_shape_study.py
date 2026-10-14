#!/usr/bin/env python3
"""
room_shape_study.py — is the rules' room too far? Three fixes, on the rules as they run from 15 Oct 2026
================================================================================
The user, 14 Oct 2026, after today's Sensex call was given a 1,609-point target (a 947-point normal day x 2 for ADX 56,
less 285 already used) on a day that moved 617: "why did new setting give target 1609 points away thats too far to book
a profit", then "yes test all three".

FIXED BEFORE ANY RESULT WAS SEEN (14 Oct 2026). BASE: the rules as live runs them from 15 Oct - the room from the day's
range alone on the ~10-day normal day (config.ROOM_LIMITS), ADX with DX over 6 (config.ADX_DX_SMOOTHING), out at T3,
the stop to T1 at T1 and to T2 at T2, the Supertrend trail after T1, the 2-hour breakeven, the T1-exit re-entry
(asserted to reproduce adx_speed_sweep_study's DX-6 figures). One change each:
    STRETCH_1_6      the ADX stretch stops at x1.6 (ADX 35+ no longer x2)
    STRETCH_NONE     no ADX stretch at all (config.TREND_RANGE_EXPANSION off)
    ROOM_CAP_1DAY    the room never more than one normal day (the 10-day mean range)
    T1_AT_1R         T1 at 1 x the risk when that is nearer than 40% of the room; T2 / T3 unchanged
A fix is kept only if it makes more than BASE in BOTH periods on Sensex + Midcap (15-minute walk) AND on Sensex's
5-minute walk (the step at the candle close).

RESULT (14 Oct 2026, history to 11 Sep 2026; room_shape_study_results.txt) - ALL THREE DROP, in both periods on both
walks. Sensex + Midcap, per lot, first two years / held-out, against BASE +649,069 / +320,345:
  STRETCH_1_6     -108,491 / -42,070     (Sensex 5-minute -73,241 / -32,248)
  STRETCH_NONE    -258,804 / -212,668    (-120,308 / -78,474) - win rate up to 47%, profit falls by half or more
  ROOM_CAP_1DAY   -84,922 / -12,782      (-64,688 / -30,120)
  T1_AT_1R        -48,909 / -26,425      (-35,171 / -36,882)
The far room is not an accident to fix: a nearer room, ceiling or T1 wins a little more often and gives up the trend
days. Days like 14 Oct (a 1,609-point room on a 617-point day) are the price of those trend days. Nothing changed live.

    python3 room_shape_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RULES = ("SENSEX", "MIDCPNIFTY")
VARIANTS = ("BASE", "STRETCH_1_6", "STRETCH_NONE", "ROOM_CAP_1DAY", "T1_AT_1R")
BASE_FIGS = {("SENSEX", "15"): (268564, 164616), ("MIDCPNIFTY", "15"): (380504, 155730), ("SENSEX", "5"): (210022, 109993)}


def _job(job):
    key, walk, variant = job
    import numpy as np
    import pandas as pd
    import config
    import backtest_intraday as bt
    import live_room_study as lrs
    import signal_engine as se
    assert config.ADX_DX_SMOOTHING["nse_index"] == 6 and config.ROOM_LIMITS["nse_index"] == "range", "live settings"
    if variant == "STRETCH_1_6":
        config.RANGE_EXPANSION_BY_ADX = [(25, 1.6), (20, 1.25), (0, 1.0)]
    elif variant == "STRETCH_NONE":
        config.TREND_RANGE_EXPANSION = False
    lrs.install_live_room(key, chain=False, normal_days=10)
    if variant == "ROOM_CAP_1DAY":
        df = bt.fetch_history(key, years=3, use_cache=True)
        day = np.array([t.date() for t in df.index])
        full = df.groupby(day).agg(h=("High", "max"), l=("Low", "min"))
        typ10 = (full["h"] - full["l"]).rolling(10).mean().shift(1).to_dict()
        inner = se.compute_reachability

        def capped(spot, oi, df_, now, **kw):
            out = dict(inner(spot, oi, df_, now, **kw))
            t = typ10.get(pd.Timestamp(now).date())
            if t is not None and t == t:
                for k_ in ("reach_up", "reach_down"):
                    if out.get(k_) is not None and out[k_] > t:
                        out[k_] = round(float(t), 2)
            return out
        se.compute_reachability = capped
    if variant == "T1_AT_1R":
        real_run = bt.run

        def run_t1(key_, df_, gate=None, **kw):
            out = real_run(key_, df_, gate=gate, **kw)
            for tr in out["trades"]:
                if tr.get("t1") is None or tr.get("stop") is None:
                    continue
                ce = tr["side"] == "CE"
                one_r = tr["entry"] + (1 if ce else -1) * abs(tr["entry"] - tr["stop"])
                if (one_r < tr["t1"]) if ce else (one_r > tr["t1"]):
                    tr["t1"] = round(one_r, 2)
            return out
        bt.run = run_t1
    if walk == "15":
        import t3_exit_recheck_study as t3
        return job, t3._job(key)[1]["t3re"]
    import t1_step_timing_study as tst
    return job, tst._job((key, "FIX"))[1]


def main():
    import multiprocessing as mp
    import stop_day_study as sds
    jobs = [(k, "15", v) for k in RULES for v in VARIANTS] + [("SENSEX", "5", v) for v in VARIANTS]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    for (k, w), (a, b) in BASE_FIGS.items():
        s = sds.split(got[(k, w, "BASE")])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), \
            f"{k} {w}: BASE must be adx_speed_sweep_study's DX 6, got {s['is']['total']:.0f} / {s['oos']['total']:.0f}"
    pool_ = lambda v: sorted([r for k in RULES for r in got[(k, "15", v)]], key=lambda r: r["when"])
    W = 172
    print("=" * W)
    print(" IS THE ROOM TOO FAR? Three fixes on the rules as they run from 15 Oct 2026. Per lot, after costs.  columns:"
          " trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print(" SENSEX + MIDCAP, 15-minute walk")
    for v in VARIANTS:
        print(sds.line("   " + v, pool_(v)))
    print(" SENSEX, 5-minute walk")
    for v in VARIANTS:
        print(sds.line("   " + v, got[("SENSEX", "5", v)]))
    for k in RULES:
        print(f" {k}, 15-minute walk")
        for v in VARIANTS:
            print(sds.line("   " + v, got[(k, "15", v)]))
    print("\n VERDICT - kept only if more than BASE in BOTH periods on Sensex + Midcap AND on Sensex's 5-minute walk")
    for v in VARIANTS[1:]:
        a = sds.verdict(v + " (Sensex + Midcap)", pool_(v), pool_("BASE")).strip()
        b = sds.verdict(v + " (Sensex 5-minute)", got[("SENSEX", "5", v)], got[("SENSEX", "5", "BASE")]).strip()
        print("  " + a)
        print("  " + b)
    return got


if __name__ == "__main__":
    main()
