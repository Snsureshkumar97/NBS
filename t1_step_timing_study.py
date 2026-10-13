#!/usr/bin/env python3
"""
t1_step_timing_study.py — WHEN the stop steps to T1 (and T2): on the touch, or once that 15-minute candle has closed
================================================================================
The user, 13 Oct 2026: "but when the stop moves to t1 its instantly exiting at t1 only". Real trades confirmed it: of
36 rules tickets closed at their T1 stop (23 Sep - 13 Oct), 35 closed within ~2 minutes of first touching T1 - inside
the same 15-minute candle - and 28 of them went on to reach T2 that day. tickets._check_price moves the stop to a rung
on the very tick that touches it and then checks it every tick, so the option's flicker around T1 closes the trade;
every study (opening_window_study.simulate_live and all built on it) moved it only from the NEXT 15-minute candle. Then
"yes measure it and build the fix".

MEASURED on the 5-minute candles (Sensex - the one rules index with 3 years of them; Nifty and Bank Nifty under the
rules for information - they run the Trend Rider live), the rules as they run now: out at T3, the stop stepping to T1
at T1 and to T2 at T2, the Supertrend trail once T1 has stepped, the 2-hour breakeven, the T1-exit re-entry waiver.
Entries, volatility and position-keeping from the rules harness; the exit walked on the 5-minute candles from the
15-minute entry; priced at the money on the real expiry (vwap_ema_atr_study.price_trade), 0.25% slippage, every charge.
FIXED BEFORE ANY RESULT WAS SEEN, two timings of the same steps:
    LIVE   the step takes effect from the next 5-minute candle after the touch - as near the live tick as candles go
           (live is, if anything, quicker still)
    FIX    the step takes effect from the next 15-MINUTE candle after the touch - what every study measured
Inside one 5-minute candle the stop as it stood comes first, then the target, then any touch. A gap through a level
fills at the open.

RESULT (13 Oct 2026, history to 11 Sep 2026; t1_step_timing_study_results.txt) - FIX KEPT and built
(config.STEP_STOP_AT_CANDLE_CLOSE = 15; step_timing_live_test.py). Sensex, first two years / held-out: on the touch
+110,037 / +97,513; from the next 15-minute candle +122,814 / +108,374 (+12,777 / +10,861), worst drop smaller in both
(-3,147 / -3,216), T1-stop exits 130 -> 97. Nifty and Bank Nifty under the rules (information): +7,194 / -3,883 and
-1,219 / +4,365 - all three +18,753 / +11,343. The live gap is likely larger: these are index levels on 5-minute
candles, and live tickets track the option's own ticks, which flicker far more around T1.

    python3 t1_step_timing_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
INDICES = ("SENSEX", "NIFTY", "BANKNIFTY")
TIMINGS = ("LIVE", "FIX")


def walk5(b5, t5, p0, tr, step_at, st15_at, entry_close_time):
    """One trade on the 5-minute candles from p0 (the first starting at/after the entry). step_at(touch candle start)
    -> when a step takes effect. st15_at(t) -> the 15-minute Supertrend known at time t. -> (exit spot, exit time,
    how, T1 touched)."""
    import pandas as pd
    o, h, l, c, day = b5
    ce = tr["side"] == "CE"
    tighten = (lambda a, b: max(a, b)) if ce else (lambda a, b: min(a, b))
    e, t1, t2, t3 = tr["entry"], tr["t1"], tr["t2"], tr["t3"]
    stop = tr["stop"]
    pend = []                                   # (effective time, level, is T1)
    t1_touched = t2_touched = t1_live = be_done = False
    d0 = day[p0]
    be_at = entry_close_time + pd.Timedelta(minutes=120)
    n = len(c)
    for p in range(p0, n):
        if day[p] != d0:
            return c[p - 1], t5[p - 1] + pd.Timedelta(minutes=5), "close", t1_touched
        start = t5[p]
        for item in [x for x in pend if x[0] <= start]:
            stop = tighten(stop, item[1])
            t1_live = t1_live or item[2]
            pend.remove(item)
        if t1_live:
            s = st15_at(start)
            if s == s:
                stop = tighten(stop, s)
        if (l[p] <= stop) if ce else (h[p] >= stop):
            return (min(o[p], stop) if ce else max(o[p], stop)), start + pd.Timedelta(minutes=5), "stop", t1_touched
        if (h[p] >= t3) if ce else (l[p] <= t3):
            return (max(o[p], t3) if ce else min(o[p], t3)), start + pd.Timedelta(minutes=5), "target", t1_touched
        if not t1_touched and ((h[p] >= t1) if ce else (l[p] <= t1)):
            t1_touched = True
            pend.append((step_at(start), t1, True))
        if not t2_touched and ((h[p] >= t2) if ce else (l[p] <= t2)):
            t2_touched = True
            pend.append((step_at(start), t2, False))
        end = start + pd.Timedelta(minutes=5)
        if not t1_touched and not be_done and end >= be_at and end.minute % 15 == 0:
            be_done = True
            if (c[p] <= e) if ce else (c[p] >= e):
                return c[p], end, "breakeven-close", t1_touched
            stop = tighten(stop, e)
        if p + 1 < n and day[p + 1] != d0 or start.time() >= __import__("datetime").time(15, 25):
            return c[p], end, "close", t1_touched
    return c[n - 1], t5[n - 1] + pd.Timedelta(minutes=5), "close", t1_touched


def _job(job):
    key, timing = job
    import math
    import numpy as np
    import pandas as pd
    import config
    import backtest_intraday as bt
    import entry_timing_study as ets
    import indicators as ind
    import pro_study as ps
    import regime_study as rs
    import reversal_exit_study as res
    import t1_reentry_study as t1r
    import vwap_ema_atr_study as vea
    ps.SLIP = 0.0025
    df = bt.fetch_history(key, years=3, use_cache=True)
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    sig = rs.features(df, rs.load_vix(), nrv)["sigma"].to_numpy()
    pre = bt.precompute(df, key)
    st15 = ind.supertrend(df, *config.supertrend_params(key))[0].to_numpy()
    adx = pre["adx"].to_numpy()
    days = set(df.index.date)
    d5 = ets.load_5m(key)
    t5 = d5.index
    b5 = (d5["Open"].to_numpy(), d5["High"].to_numpy(), d5["Low"].to_numpy(), d5["Close"].to_numpy(),
          np.array([t.date() for t in t5]))
    BAR15 = pd.Timedelta(minutes=15)

    def st15_at(t):                              # the last 15-minute candle CLOSED by time t
        k = int(df.index.searchsorted(t - BAR15, side="right")) - 1
        return st15[k] if k >= 0 else float("nan")

    def adx_at(t):
        k = int(df.index.searchsorted(t - BAR15, side="right")) - 1
        return float(adx[k]) if k >= 0 else 0.0

    if timing == "LIVE":
        step_at = lambda s: s + pd.Timedelta(minutes=5)
    else:
        step_at = lambda s: s.floor("15min") + BAR15
    trades = bt.run(key, df, gate=lambda i, rec: res.live_gate(key, i, rec, df))["trades"]
    pos = {t: n for n, t in enumerate(df.index)}
    rows = []
    for tr in trades:
        i = pos[tr["when"]]
        s = sig[i]
        if not (s == s) or s <= 0 or tr.get("t3") is None:
            continue
        t_in = df.index[i] + BAR15
        p0 = int(t5.searchsorted(t_in))
        if p0 >= len(t5) or t5[p0].date() != t_in.date():
            continue
        px, t_out, how, t1_touched = walk5(b5, t5, p0, tr, step_at, st15_at, t_in)
        net = vea.price_trade(key, days, t_in, tr["entry"], t_out, px, s, tr["side"] == "CE")
        if net is None:
            continue
        rows.append({"when": t_in, "exit_time": t_out, "net": net, "side": tr["side"], "index": key,
                     "closed_via": "target" if how == "target" else "stop" if abs(px - tr["stop"]) < 1e-6 else "other",
                     "exit_adx": adx_at(t_out), "t1_exit": how == "stop" and t1_touched,
                     "t1_stop_exit": how == "stop" and t1_touched and abs(px - tr["t1"]) < 1e-6 * max(1, px)})
    rows.sort(key=lambda r: r["when"])
    kept = t1r.sequential(rows, config.REENTRY_COOLDOWN_MIN, config.ADX_TREND_THRESHOLD, "A")
    return job, kept


def main():
    from concurrent.futures import ProcessPoolExecutor
    import stop_day_study as sds
    with ProcessPoolExecutor(max_workers=6) as ex:
        got = dict(ex.map(_job, [(k, t) for k in INDICES for t in TIMINGS]))
    W = 172
    print("=" * W)
    print(" WHEN THE STOP STEPS TO T1 / T2 - the rules as they run now (out at T3, T1-exit re-entry), walked on 5-minute"
          " candles. Per lot, after costs.")
    print(" columns: trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    label = {"LIVE": "LIVE: on the touch (next 5-min candle)", "FIX": "FIX: from the next 15-min candle"}
    for k in INDICES:
        tag = "  <- runs the rules live" if k == "SENSEX" else "  (runs the Trend Rider live - for information)"
        print(f" {k}{tag}")
        for t in TIMINGS:
            rows = got[(k, t)]
            print(sds.line("   " + label[t], rows))
        for t in TIMINGS:
            rows = got[(k, t)]
            t1s = [r for r in rows if r.get("t1_stop_exit")]
            print(f"     {label[t]:44s} closed at the T1 stop: {len(t1s):4d} of {len(rows)} trades")
    pool = lambda t: sorted([r for k in INDICES for r in got[(k, t)]], key=lambda r: r["when"])
    print(" ALL THREE (for information)")
    for t in TIMINGS:
        print(sds.line("   " + label[t], pool(t)))
    print("\n VERDICT - the fix against live, kept only if more profit in BOTH periods")
    print("  " + sds.verdict("FIX on Sensex", got[("SENSEX", "FIX")], got[("SENSEX", "LIVE")]).strip())
    print("  " + sds.verdict("FIX on all three", pool("FIX"), pool("LIVE")).strip())
    return got


if __name__ == "__main__":
    main()
