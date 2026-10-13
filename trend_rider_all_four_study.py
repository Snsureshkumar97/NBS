#!/usr/bin/env python3
"""
trend_rider_all_four_study.py — the Trend Rider as it runs today, on all four indices, beside the tool's own rules
================================================================================
The user, 13 Oct 2026: "can you check what is the exit for trend rider and how id trend rider results on all for four
index".

THE TREND RIDER LIVE (config.TREND_RIDER, NEAR_TARGET_STEPS): entered on the first 15-minute close above EMA 50 and
VWAP with ADX(14) over 20 and rising and +DI over -DI (mirrored for a put); stop the last 5 candles' low (high); one
target at 2.75 x the risk, its T1 / T2 / T3 a third, two thirds and all of it; 90% of the way to T2 the stop moves to
T1, 90% of the way to T3 to T2; out at T3, the stop, or by 15:15; one position at a time. Live on Nifty and Bank Nifty
(config.SYSTEM_DEFAULTS); Sensex and Midcap Select run the tool's own rules.

Walked as trend_rider_t1_study.py walks it - on the 5-minute candles where there are 3 years of them (Nifty, Bank
Nifty, Sensex) and on the 15-minute candles for all four (Midcap Select has no 5-minute history); priced at the money
on the real expiry (Midcap monthly), 0.25% slippage a side, every charge, per lot. The rules' figures beside it are
index_candidates_study's (bollinger_vote_study.BASELINE): the live entry checks and exit on 15-minute candles.

RESULT (13 Oct 2026, history to 11 Sep 2026, per lot after costs; trend_rider_all_four_study_results.txt), first two
years / held-out year, Trend Rider (5-minute walk where there is one) against the rules:
    Nifty       +179,191 / +81,254   vs  +154,936 / +30,386   Trend Rider better both - live on it
    Bank Nifty  +159,890 / +33,779   vs  +172,293 / -5,376    Trend Rider better held-out - live on it
    Sensex      +205,869 / +42,314   vs  +110,280 / +100,217  mixed: better first two years, far worse held-out
    Midcap      +122,064 / -11,494   vs  +141,945 / +86,451   rules better both
  Today's mix (Trend Rider on Nifty + Bank Nifty, rules on Sensex + Midcap) +591,306 / +301,701 - against all four on the
  Trend Rider +656,023 / +141,170 (15-minute walk) or all four on the rules +579,454 / +211,678: the best held-out year
  of the three. Nothing changed.

    python3 trend_rider_all_four_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
INDICES = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
T_TODAY = "today: 90% to T2 -> T1, 90% to T3 -> T2"


def _job(k):
    """The live Trend Rider on one index: {"5": rows or None, "15": rows}."""
    import trend_rider_t1_study as trs
    RR = trs.RR
    trs.VARIANTS = [("no steps (the study's own check)", None, None, False),
                    (T_TODAY, [(RR * 2 / 3 * 0.9, RR / 3), (RR * 0.9, RR * 2 / 3)], "ladder", False)]
    if k != "MIDCPNIFTY":
        _, out = trs._job(k)
        return k, {"5": out[(T_TODAY, "5")], "15": out[(T_TODAY, "15")]}
    # Midcap Select: not in stop_day_study's context and no 5-minute history - the 15-minute walk built the same way,
    # its options on their monthly expiries (index_candidates_study.py)
    import datetime as dt
    import math
    import numpy as np
    import pandas as pd
    import backtest_intraday as bt
    import index_candidates_study as ics
    import pro_study as ps
    import regime_study as rs
    import vwap_ema_atr_study as vea
    ps.SLIP = 0.0025
    real = ps._scheduled
    ps._scheduled = lambda kk, d, real=real: ics._monthly(d) if kk == k else real(kk, d)
    df = bt.fetch_history(k, years=3, use_cache=True)[["Open", "High", "Low", "Close", "Volume"]]
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    sig = rs.features(df, rs.load_vix(), nrv)["sigma"]
    sigma_by_day = sig.groupby(sig.index.date).first().to_dict()
    days = set(df.index.date)
    last15 = (dt.datetime.combine(dt.date(2000, 1, 1), trs.OUT_BY) - dt.timedelta(minutes=15)).time()
    tod15 = np.array([t.time() for t in df.index])
    b15 = (df["Open"].to_numpy(), df["High"].to_numpy(), df["Low"].to_numpy(), df["Close"].to_numpy(),
           np.array([t.date() for t in df.index]))
    steps = trs.VARIANTS[1][1]
    rows, free = [], -1
    for i, side, entry, stop in trs.entries(df):
        if i <= free:
            continue
        risk = entry - stop if side == "CE" else stop - entry
        t_in = df.index[i] + trs.BAR15
        px, j, via = trs.walk(b15, i + 1, side, entry, stop, risk, steps, "ladder", False, lambda j: np.nan,
                              tod15 >= last15)
        s = sigma_by_day.get(t_in.date())
        net = vea.price_trade(k, days, t_in, entry, df.index[j] + trs.BAR15, px, s, side == "CE") if s and s == s else None
        if net is not None:
            rows.append({"when": t_in, "exit_time": df.index[j] + trs.BAR15, "net": net, "side": side,
                         "closed_via": via, "index": k})
        free = j
    return k, {"5": None, "15": rows}


def main():
    from concurrent.futures import ProcessPoolExecutor
    import bollinger_vote_study as bvs
    import stop_day_study as sds
    with ProcessPoolExecutor(max_workers=4) as ex:
        got = dict(ex.map(_job, INDICES))
    # the 5-minute walk on Nifty and Bank Nifty must be the live exit's own figures (exit_ladder_study, 13 Oct 2026)
    for k, (a, b) in {"NIFTY": (179191, 81254), "BANKNIFTY": (159890, 33779)}.items():
        s = sds.split(got[k]["5"])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), f"{k}: the live Trend Rider's figures"
    W = 172
    print("=" * W)
    print(" THE TREND RIDER (live exit) ON ALL FOUR INDICES, beside the tool's own rules. Per lot, after costs.  columns:"
          " trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    for k in INDICES:
        live = "Trend Rider" if k in ("NIFTY", "BANKNIFTY") else "the tool's rules"
        print(f" {k}  (live today: {live})")
        if got[k]["5"] is not None:
            print(sds.line("   Trend Rider, 5-minute walk", got[k]["5"]))
        print(sds.line("   Trend Rider, 15-minute walk", got[k]["15"]))
        a, b = bvs.BASELINE[k]
        print(f"   {'the tool rules (15-minute)':46s} {'':>5} {'':>4}  ₹{a:>+10,.0f}  {'':54s}|  {'':>4} {'':>4}  ₹{b:>+10,.0f}")
    print(" ALL FOUR, the Trend Rider on the 15-minute walk")
    print(sds.line("   Trend Rider on all four", sorted([r for k in INDICES for r in got[k]["15"]], key=lambda r: r["when"])))
    ta = sum(bvs.BASELINE[k][0] for k in INDICES)
    tb = sum(bvs.BASELINE[k][1] for k in INDICES)
    print(f"   {'the tool rules on all four':46s} {'':>5} {'':>4}  ₹{ta:>+10,.0f}  {'':54s}|  {'':>4} {'':>4}  ₹{tb:>+10,.0f}")
    return got


if __name__ == "__main__":
    main()
