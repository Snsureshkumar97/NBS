#!/usr/bin/env python3
"""
live_room_study.py — the rules' 3-year test with the room to run the tool really uses LIVE
================================================================================
The user, 14 Oct 2026, after room_to_run_accuracy_study.py found the backtest's room about twice live's: "yes run
step 1" - what does the live system really earn?

Every 3-year rules study (t3_exit_recheck_study, t1_step_timing_study, ...) builds its room to run from
backtest_intraday.run's call to signal_engine.compute_reachability with NO option chain - so no expected-move limit -
and its "normal day" the mean of every prior day since 2023. Live, the same function gets the real chain (the ATM
straddle -> the expected move for the time left) and a normal day of the ~10 prior days in its 15 calendar days of
candles. The room sets T1 / T2 / T3 (40 / 70 / 100% of it) and the reward:risk gate.

FIXED BEFORE ANY RESULT WAS SEEN (14 Oct 2026): the SAME function, called the way live calls it -
  BACKTEST  as every study ran it (asserted to reproduce t3_exit_recheck_study's / t1_step_timing_study's figures)
  LIVE      an option chain at each candle: the at-the-money call and put priced (Black-Scholes) from the index's own
            VIX-scaled volatility on its real expiry - what the backtests already price options with - and the normal
            day the mean of the 10 prior days. The OI walls stay out (no history of them); live they can only tighten
            the room further.
Both through the rules as they run now: out at T3, the stop to T1 at T1 and to T2 at T2, the Supertrend trail after
T1, the 2-hour breakeven, the T1-exit re-entry (t3_exit_recheck_study's "t3re", 15-minute candles, all four indices),
and for Sensex the 5-minute walk with the step at the candle close (t1_step_timing_study's FIX). Per lot, after costs.

RESULT (14 Oct 2026, history to 11 Sep 2026; live_room_study_results.txt) - LIVE EARNS FAR LESS THAN THE 3-YEAR
FIGURES SAID. Per lot, first two years / held-out:
  Sensex + Midcap, 15-minute: backtest room +370,798 / +229,174, live room +287,984 / +142,514 (-82,814 / -86,660)
  Sensex, 5-minute (the most faithful walk): +122,814 / +108,374 -> +13,713 / +19,727 - nearly break-even (PF 1.04 / 1.09)
  Sensex 15-minute +176,460 / +135,052 -> +78,098 / +55,603; Midcap +194,339 / +94,122 -> +209,886 / +86,911 (about level)
  The live room's targets are about half as far (Sensex median room 256 points against 528): more trades win (37-43% ->
  48-52%) and far less is made - the runners are cut short - and the reward:risk gate turns away more entries (1,434 ->
  1,227). The Trend Rider and the TradingView rating (Nifty, Bank Nifty) set their own targets and are not affected.
  Nothing changed live.

    python3 live_room_study.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
INDICES = ("SENSEX", "MIDCPNIFTY", "NIFTY", "BANKNIFTY")
RULES_LIVE = ("SENSEX", "MIDCPNIFTY")                 # the indices that run the rules live
T3RE = {"SENSEX": (176460, 135052), "MIDCPNIFTY": (194339, 94122)}     # t3_exit_recheck_study_results.txt
SENSEX5 = (122814, 108374)                                              # t1_step_timing_study_results.txt, FIX
_ROOMS = []


def install_live_room(key):
    """Wrap signal_engine.compute_reachability in this process: the option chain and normal day live would hand it."""
    import numpy as np
    import pandas as pd
    import config
    import backtest_intraday as bt
    import pro_study as ps
    import regime_study as rs
    import signal_engine as se
    df = bt.fetch_history(key, years=3, use_cache=True)
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    sig = rs.features(df, rs.load_vix(), nrv)["sigma"]
    sig_at = dict(zip(df.index, sig.to_numpy()))
    day = pd.Series([t.date() for t in df.index])
    full = df.groupby(day.to_numpy()).agg(h=("High", "max"), l=("Low", "min"))
    typ10 = (full["h"] - full["l"]).rolling(10).mean().shift(1).to_dict()
    days = set(df.index.date)
    step = config.INSTRUMENTS[key]["strike_step"]
    real = se.compute_reachability

    def live_like(spot, oi, df_, now, adx=None, range_stats=None, index_key=None, day_extremes=None):
        ts = pd.Timestamp(now)
        s = sig_at.get(ts)
        chain = oi
        if s is not None and s == s and s > 0:
            exp = ps.expiry_on_or_after(key, ts.date(), days)
            K = round(spot / step) * step
            T = ps.years_to(exp, ts)
            chain = {"available": True, "expiry": exp.isoformat(),
                     "strikes": [{"strike": K, "call_ltp": rs.bs(spot, K, T, s, True),
                                  "put_ltp": rs.bs(spot, K, T, s, False)}]}
        if range_stats is not None:
            t10 = typ10.get(ts.date())
            if t10 is not None and t10 == t10 and t10 > 0:
                range_stats = (float(t10), range_stats[1])
        out = real(spot, chain, df_, now, adx=adx, range_stats=range_stats, index_key=index_key,
                   day_extremes=day_extremes)
        _ROOMS.append((out.get("reach_up"), out.get("reach_down")))
        return out
    se.compute_reachability = live_like


def _job(job):
    key, walk, mode = job
    if mode == "LIVE":
        install_live_room(key)
    if walk == "15":
        import t3_exit_recheck_study as t3
        _, out = t3._job(key)
        rows = out["t3re"]
    else:
        import t1_step_timing_study as tst
        _, rows = tst._job((key, "FIX"))
    return job, rows


def main():
    import multiprocessing as mp
    import stop_day_study as sds
    jobs = [(k, "15", m) for k in INDICES for m in ("BACKTEST", "LIVE")] + [("SENSEX", "5", m) for m in ("BACKTEST", "LIVE")]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    for k, (a, b) in T3RE.items():
        s = sds.split(got[(k, "15", "BACKTEST")])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), f"{k}: BACKTEST must be t3_exit_recheck's t3re"
    s = sds.split(got[("SENSEX", "5", "BACKTEST")])
    assert (round(s["is"]["total"]), round(s["oos"]["total"])) == SENSEX5, "Sensex 5-minute BACKTEST must be FIX"
    W = 172
    print("=" * W)
    print(" THE RULES WITH THE ROOM TO RUN LIVE USES (expected move + 10-day normal day) vs THE BACKTEST'S ROOM. Per lot,"
          " after costs.  columns: trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    pool_ = lambda walk, mode, ks: sorted([r for k in ks for r in got[(k, walk, mode)]], key=lambda r: r["when"])
    print(" SENSEX + MIDCAP (run the rules live), 15-minute walk")
    for m in ("BACKTEST", "LIVE"):
        print(sds.line(f"   {m} room", pool_("15", m, RULES_LIVE)))
    print(" SENSEX, 5-minute walk (the step at the candle close)")
    for m in ("BACKTEST", "LIVE"):
        print(sds.line(f"   {m} room", got[("SENSEX", "5", m)]))
    for k in INDICES:
        print(f" {k}{'' if k in RULES_LIVE else '  (runs the Trend Rider / TradingView rating live - information)'}")
        for m in ("BACKTEST", "LIVE"):
            print(sds.line(f"   {m} room", got[(k, "15", m)]))
    print("\n LIVE room against the BACKTEST room (what the 3-year figures overstate or understate)")
    print("  " + sds.verdict("Sensex + Midcap, 15-minute", pool_("15", "LIVE", RULES_LIVE), pool_("15", "BACKTEST", RULES_LIVE)).strip())
    print("  " + sds.verdict("Sensex, 5-minute", got[("SENSEX", "5", "LIVE")], got[("SENSEX", "5", "BACKTEST")]).strip())
    print("  " + sds.verdict("all four, 15-minute (info)", pool_("15", "LIVE", INDICES), pool_("15", "BACKTEST", INDICES)).strip())
    return got


if __name__ == "__main__":
    main()
