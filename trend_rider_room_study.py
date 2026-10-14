#!/usr/bin/env python3
"""
trend_rider_room_study.py — a room-to-run check on the Trend Rider (Nifty + Bank Nifty, 3 years)
================================================================================
The user, 14 Oct 2026: "test adding a room check to trend rider". The Trend Rider never asks how far the market can
go: its one target is 2.75 x its own risk (the last 5 candles' low / high to the entry), T1 / T2 / T3 a third, two thirds
and all of it.

FIXED BEFORE ANY RESULT WAS SEEN (14 Oct 2026). The Trend Rider as it runs live - its entries
(trend_rider_t1_study.entries), its live exit (TREND_RIDER_EXIT "rules": the stop to T1 at T1 and to T2 at T2 once that
15-minute candle closes, the Supertrend trail, the 2-hour breakeven; out at the target, the stop or 15:15), walked on the
5-minute candles and priced at the money on the real expiry, 0.25% slippage, every charge (tv_ratings_study's own walk,
asserted to reproduce the live figures). The room to run is signal_engine.compute_reachability at the entry candle as
live computes it now (config.ROOM_LIMITS: the day's range alone - the ~10-day normal day x the ADX stretch, less what the
day has used, with the trend-day room), in the trade's direction:
    TODAY            no room check
    SKIP_NO_ROOM     no trade when the room is shorter than the target (2.75 x the risk)
    SKIP_UNDER_1R    no trade when the room is under 1 x the risk (the rules' own reward:risk gate)
    CAP_AT_ROOM      the target cut to the room when the room is shorter (T1 / T2 thirds of it); no trade under 1R
    (info) SKIP_NO_ROOM_OLD   SKIP_NO_ROOM with the room as live had it before 14 Oct (the expected move a limit too)
A check is kept only if it makes more than TODAY in BOTH the first two years and the held-out year, the two pooled.

RESULT (14 Oct 2026, history to 11 Sep 2026; trend_rider_room_study_results.txt) - ALL DROP, nothing changed. Nifty +
Bank Nifty, per lot, first two years / held-out: TODAY +275,631 / +121,022; SKIP_NO_ROOM -46,512 / +31,793 (skips 614 +
593 signals); SKIP_UNDER_1R +29,385 / +72,589; CAP_AT_ROOM -43,296 / +45,381; (info) the old room -6,533 / +9,683. The
trades a room check turns away are the trend days that blow through a normal day's range - where the Trend Rider earns
its money (as the range-exhaustion rule found for the rules on 2 Oct). Win rates rise a little; profit collapses.

    python3 trend_rider_room_study.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TREND = ("NIFTY", "BANKNIFTY")
TODAY_FIGS = {"NIFTY": (157331, 70163), "BANKNIFTY": (118299, 50859)}    # trend_rider_rules_stop_study B (live exit)
VARIANTS = ("TODAY", "SKIP_NO_ROOM", "SKIP_UNDER_1R", "CAP_AT_ROOM", "SKIP_NO_ROOM_OLD")


def _job(job):
    k, variant = job
    import datetime as dt
    import numpy as np
    import pandas as pd
    import config
    import backtest_intraday as bt
    import entry_timing_study as ets
    import indicators as ind
    import live_room_study as lrs
    import pro_study as ps
    import regime_study as rs
    import signal_engine as se
    import trend_rider_t1_study as trs
    import tv_ratings_study as tvs
    import vwap_ema_atr_study as vea
    ps.SLIP = 0.0025
    if variant == "SKIP_NO_ROOM_OLD":
        lrs.install_live_room(k, chain=True, normal_days=10)       # the room before ROOM_LIMITS (pins nse_index "all")
    df = bt.fetch_history(k, years=3, use_cache=True)[["Open", "High", "Low", "Close", "Volume"]]
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    sig = rs.features(df, rs.load_vix(), nrv)["sigma"]
    sigma_by_day = sig.groupby(sig.index.date).first().to_dict()
    days = set(df.index.date)
    BAR15 = pd.Timedelta(minutes=15)
    pre = bt.precompute(df, k)
    day = np.array([t.date() for t in df.index])
    full = df.groupby(day).agg(h=("High", "max"), l=("Low", "min"))
    typ10 = (full["h"] - full["l"]).rolling(10).mean().shift(1).to_dict()
    st15 = ind.supertrend(df, *config.supertrend_params(k))[0].to_numpy()
    d5 = ets.load_5m(k)
    t5 = d5.index
    bars = (d5["Open"].to_numpy(), d5["High"].to_numpy(), d5["Low"].to_numpy(), d5["Close"].to_numpy(),
            np.array([x.date() for x in t5]))
    cut = np.array([x.time() >= (dt.datetime.combine(dt.date(2000, 1, 1), trs.OUT_BY) - dt.timedelta(minutes=5)).time()
                    for x in t5])
    p15 = df.index.searchsorted(t5 - BAR15, side="right") - 1
    st_at = lambda p: st15[p15[p]] if p15[p] >= 0 else np.nan
    lo, hi, cl = df["Low"].to_numpy(), df["High"].to_numpy(), df["Close"].to_numpy()
    RR, SWING = trs.RR, config.TREND_RIDER["swing"]

    def room(i, side):
        t10 = typ10.get(day[i])
        if t10 is None or t10 != t10:
            return None
        r = se.compute_reachability(float(cl[i]), {"available": False}, df, df.index[i].to_pydatetime(),
                                    adx=float(pre["adx"].iloc[i]), range_stats=(float(t10), float(pre["used_today"].iloc[i])),
                                    index_key=k, day_extremes=(float(pre["day_high"].iloc[i]), float(pre["day_low"].iloc[i])))
        return r.get("reach_up") if side == "CE" else r.get("reach_down")

    rows, free, skipped = [], -1, 0
    for i, side, entry, stop in trs.entries(df):
        if i <= free:
            continue
        risk = entry - stop if side == "CE" else stop - entry
        lv = lambda r_: entry + r_ * risk if side == "CE" else entry - r_ * risk
        tgt_r = RR
        if variant != "TODAY":
            rm = room(i, side)
            rm_r = (rm / risk) if rm else 0.0
            if variant in ("SKIP_NO_ROOM", "SKIP_NO_ROOM_OLD") and rm_r < RR:
                skipped += 1
                continue
            if variant == "SKIP_UNDER_1R" and rm_r < 1.0:
                skipped += 1
                continue
            if variant == "CAP_AT_ROOM":
                if rm_r < 1.0:
                    skipped += 1
                    continue
                tgt_r = min(RR, rm_r)
        rungs = [(lv(tgt_r / 3), lv(tgt_r / 3)), (lv(tgt_r * 2 / 3), lv(tgt_r * 2 / 3))]
        t_in = df.index[i] + BAR15
        p0 = int(t5.searchsorted(t_in))
        if p0 >= len(t5) or t5[p0].date() != t_in.date():
            continue
        px, p, via = tvs.walk(bars, t5, p0, 5, side, entry, stop, lv(tgt_r), rungs, st_at,
                              t_in + pd.Timedelta(minutes=120), None, None, cut)
        t_out = t5[p] + pd.Timedelta(minutes=5)
        s = sigma_by_day.get(t_in.date())
        net = vea.price_trade(k, days, t_in, entry, t_out, px, s, side == "CE") if s and s == s else None
        if net is not None:
            rows.append({"when": t_in, "exit_time": t_out, "net": net, "side": side, "closed_via": via, "index": k})
        free = int(df.index.searchsorted(t5[p], side="right")) - 1
    return job, (rows, skipped)


def main():
    import multiprocessing as mp
    import stop_day_study as sds
    jobs = [(k, v) for k in TREND for v in VARIANTS]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    for k, (a, b) in TODAY_FIGS.items():
        s = sds.split(got[(k, "TODAY")][0])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), \
            f"{k}: TODAY must be the live Trend Rider, got {s['is']['total']:.0f} / {s['oos']['total']:.0f}"
    pool_ = lambda v: sorted([r for k in TREND for r in got[(k, v)][0]], key=lambda r: r["when"])
    W = 172
    print("=" * W)
    print(" A ROOM-TO-RUN CHECK ON THE TREND RIDER (Nifty + Bank Nifty, 5-minute walk, its live exit). Per lot, after costs."
          "  columns: trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print(" NIFTY + BANK NIFTY")
    for v in VARIANTS:
        print(sds.line("   " + v, pool_(v)))
    for k in TREND:
        print(f" {k}")
        for v in VARIANTS:
            print(sds.line("   " + v, got[(k, v)][0]) + f"   ({got[(k, v)][1]} signals skipped)")
    print("\n VERDICT - kept only if more than TODAY in BOTH periods (the two pooled)")
    for v in VARIANTS[1:]:
        print("  " + sds.verdict(v + (" (info)" if v.endswith("OLD") else ""), pool_(v), pool_("TODAY")).strip())
    return got


if __name__ == "__main__":
    main()
