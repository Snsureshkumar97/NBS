#!/usr/bin/env python3
"""
indian_strategies_study.py — the popular Indian intraday index strategies not yet tested, against the tool
================================================================================
The user, 22 Oct 2026: "so in all strategy available in the internet for indian markets only my tool strategy is good
... look for it till you find one". Already tested since September and NOT better: VWAP + EMA 9/20 + ATR (5-min), the
GitHub Supertrend + Donchian Nifty algo, retest entries, +10-point scalps, CPR, pivots, order blocks, Bollinger, candle
patterns, Gann, RSI caps, opening-window skips, confirmation entries, option-selling condors and RSI(2). Two outside
strategies DID beat it and were built into it: the pasted Trend Rider and the TradingView rating. This tests the
well-known ones still missing, each in its commonly published form.

ALL ON THE SAME FOOTING AS THE TOOL: 3 years of 15-minute candles (5-minute for the first-5-minute rule), the at-the-money
option of the real expiry priced as pro_study.price does (Black-Scholes on the day's sigma, slippage 0.25%, Zerodha's
charges), per lot at today's lot sizes, one position at a time per index, out by 15:15 (15:30 for the last-half-hour
rule). In-sample to 15 Aug 2025, then held out.

FIXED BEFORE ANY RESULT WAS SEEN (22 Oct 2026):
  ORB15     the first 15-minute candle's high / low; a call on the first 15-minute close above the high, a put below the
            low (09:30-14:30); stop the range's other end; target twice the risk; one trade a day
  ORB30     the same on the first 30 minutes' range
  FIRST5    the first 5-minute candle's high / low, 5-minute closes, the same stop / target (Nifty, Bank Nifty, Sensex)
  PDHL      the previous day's high / low: a call on a 15-minute close above it, a put below its low; stop the breakout
            candle's other end; target twice the risk; one trade a day
  CAMARILLA the previous day's H4 / L4 (close +/- 1.1 x range / 2): a call on a close above H4, a put below L4; stop H3 /
            L3 (close +/- 1.1 x range / 4); target twice the risk; one trade a day
  NOISE     Zarattini, Aziz & Barbon ("Beat the Market"): bands max(open, previous close) x (1 + s) and min(...) x (1 - s),
            s = the 14-day average move from the open at that time of day; checked every 30 minutes (10:00 ... 15:00): a
            call above the upper band, a put below the lower; out when a 30-minute check closes back past the band or
            VWAP (whichever is nearer); out at 15:15
  SUPERTR   Supertrend (10, 3) on 15 minutes: a call when it turns up, a put when it turns down (09:30-14:30); out on
            the opposite turn or at 15:15
  LAST30    Gao, Han, Li & Zhou ("Market intraday momentum", JFE 2018): the first half hour's move (previous close to
            09:45) decides; a call at 15:00 if it was up, a put if down; out at 15:30
  BETTER only if it earns more than the tool's own system on that index in BOTH periods - the system each index runs:
  the TradingView rating on Nifty and Bank Nifty, the rules on Sensex and Midcap (also shown: the rules on Nifty).

RESULT (22 Oct 2026, history to 11 Sep 2026; indian_strategies_study_results.txt). On Nifty and Bank Nifty nothing beats
the tool's TradingView rating. Against the rules, five earn more in both periods: on Sensex ORB15 (+190,507 / +57,285),
ORB30 (+238,231 / +62,588), CAMARILLA (+215,988 / +75,447) and SUPERTR (+210,369 / +65,690) against +78,098 / +55,603;
on Midcap SUPERTR (+250,176 / +105,985) against +209,886 / +86,911. CAMARILLA also beats the rules on Nifty (+217,160 /
+70,078 against +60,462 / +51,814) and made money on Nifty, Sensex and Midcap in both periods. PDHL and LAST30 (the
published last-half-hour momentum) do nothing useful; NOISE is strong in-sample, weak held-out. ~30 comparisons, so the
winners went on to indian_strategies_robustness.py.

    /usr/bin/python3 indian_strategies_study.py
"""
import datetime as dt
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
FIVE = ("NIFTY", "BANKNIFTY", "SENSEX")
STRATS = ("ORB15", "ORB30", "FIRST5", "PDHL", "CAMARILLA", "NOISE", "SUPERTR", "LAST30")
IST = "Asia/Kolkata"
T = lambda d, hm: pd.Timestamp(f"{d} {hm}", tz=IST)


def opt_net(key, days, t_in, t_out, s_in, s_out, side, sigma):
    """pro_study.price's rupees per lot, for explicit entry and exit times."""
    import pro_study as ps
    import regime_study as rs
    meta = config.INSTRUMENTS[key]
    step, qty = meta["strike_step"], meta["lot_size"]
    exch = "BSE" if meta["kite_exchange"] == "BSE" else "NSE"
    exp = ps.expiry_on_or_after(key, t_in.date(), days)
    call = side == "CE"
    K = round(s_in / step) * step
    p0 = rs.bs(s_in, K, ps.years_to(exp, t_in), sigma, call)
    if p0 <= 0.5:
        return None
    buy = p0 * (1 + ps.SLIP) * qty
    p1 = rs.bs(s_out, K, ps.years_to(exp, t_out), sigma, call)
    sells = max(p1 * (1 - ps.SLIP), 0.0) * qty
    brokerage = rs.BROKERAGE_PER_ORDER * 2
    txn = rs.TXN[exch] * (buy + sells)
    sebi = rs.SEBI_PER_RUPEE * (buy + sells)
    return sells - buy - (brokerage + rs.STT_SELL * sells + txn + sebi + rs.STAMP_BUY * buy + rs.GST * (brokerage + txn + sebi))


def walk(bars, start, side, stop, target, custom_exit=None, last="15:15"):
    """From bar `start` (entered at its close): the first bar whose range takes the stop (checked first) or the target;
    custom_exit(j) -> True exits at that bar's close; otherwise the close of the last bar ending by `last`.
    -> (exit price, exit bar)."""
    d = bars.index[start].date()
    bar_len = bars.index[1] - bars.index[0] if len(bars) > 1 else pd.Timedelta(minutes=15)
    j = start
    for j in range(start + 1, len(bars)):
        if bars.index[j].date() != d or bars.index[j] + bar_len > T(d, last):
            return float(bars["Close"].iloc[j - 1]), j - 1
        hi, lo = float(bars["High"].iloc[j]), float(bars["Low"].iloc[j])
        if stop is not None and ((lo <= stop) if side == "CE" else (hi >= stop)):
            return stop, j
        if target is not None and ((hi >= target) if side == "CE" else (lo <= target)):
            return target, j
        if custom_exit and custom_exit(j):
            return float(bars["Close"].iloc[j]), j
    return float(bars["Close"].iloc[j]), j


def run(key, strat):
    import backtest_intraday as bt
    import entry_timing_study as ets
    import index_candidates_study as ics
    import indicators as ind
    import pro_study as ps
    import regime_study as rs
    ps.SLIP = 0.0025
    if key == "MIDCPNIFTY":
        real = ps._scheduled
        ps._scheduled = lambda k, d, real=real: ics._monthly(d) if k == key else real(k, d)
    df = bt.fetch_history(key, years=3, use_cache=True)
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    sig = rs.features(df, rs.load_vix(), nrv)["sigma"]
    sig_day = sig.groupby(sig.index.date).first().to_dict()
    days = set(df.index.date)
    bars = df
    if strat == "FIRST5":
        if key not in FIVE:
            return []
        bars = ets.load_5m(key)
    bar_len = bars.index[1] - bars.index[0]
    day_of = np.array([t.date() for t in bars.index])
    daily = df.groupby(df.index.date).agg(H=("High", "max"), L=("Low", "min"), C=("Close", "last"), O=("Open", "first"))
    dlist = list(daily.index)
    prev = {dlist[n]: daily.iloc[n - 1] for n in range(1, len(dlist))}
    trades = []
    busy_until = None

    def take(i, side, stop, target, custom=None, last="15:15"):
        nonlocal busy_until
        t_in = bars.index[i] + bar_len
        px_out, j = walk(bars, i, side, stop, target, custom, last)
        t_out = bars.index[j] + bar_len
        s = sig_day.get(bars.index[i].date())
        if not (s == s) or s is None or s <= 0:
            return
        net = opt_net(key, days, t_in, t_out, float(bars["Close"].iloc[i]), px_out, side, float(s))
        if net is not None:
            trades.append({"when": t_in, "net": net, "index": key, "side": side})
        busy_until = t_out

    starts = np.r_[0, np.where(day_of[1:] != day_of[:-1])[0] + 1]
    for n, s0 in enumerate(starts):
        e0 = starts[n + 1] if n + 1 < len(starts) else len(bars)
        d = day_of[s0]
        if d not in prev:
            continue
        pv = prev[d]
        if strat in ("ORB15", "ORB30", "FIRST5"):
            k = {"ORB15": 1, "ORB30": 2, "FIRST5": 1}[strat]
            if e0 - s0 <= k:
                continue
            rh, rl = float(bars["High"].iloc[s0:s0 + k].max()), float(bars["Low"].iloc[s0:s0 + k].min())
            for i in range(s0 + k, e0):
                if bars.index[i] + bar_len > T(d, "14:30"):
                    break
                c = float(bars["Close"].iloc[i])
                if c > rh:
                    take(i, "CE", rl, c + 2 * (c - rl)); break
                if c < rl:
                    take(i, "PE", rh, c - 2 * (rh - c)); break
        elif strat in ("PDHL", "CAMARILLA"):
            if strat == "PDHL":
                up, dn = float(pv.H), float(pv.L)
            else:
                rg = float(pv.H - pv.L)
                up, dn = float(pv.C) + 1.1 * rg / 2, float(pv.C) - 1.1 * rg / 2
                st_up, st_dn = float(pv.C) + 1.1 * rg / 4, float(pv.C) - 1.1 * rg / 4
            for i in range(s0 + 1, e0):
                if bars.index[i] + bar_len > T(d, "14:30"):
                    break
                c = float(bars["Close"].iloc[i])
                if c > up:
                    stop = float(bars["Low"].iloc[i]) if strat == "PDHL" else st_up
                    if stop >= c:
                        break
                    take(i, "CE", stop, c + 2 * (c - stop)); break
                if c < dn:
                    stop = float(bars["High"].iloc[i]) if strat == "PDHL" else st_dn
                    if stop <= c:
                        break
                    take(i, "PE", stop, c - 2 * (stop - c)); break
        elif strat == "LAST30":
            i945 = [i for i in range(s0, e0) if bars.index[i] + bar_len == T(d, "09:45")]
            i1500 = [i for i in range(s0, e0) if bars.index[i] + bar_len == T(d, "15:00")]
            if not i945 or not i1500:
                continue
            r = float(bars["Close"].iloc[i945[0]]) - float(pv.C)
            if r == 0:
                continue
            take(i1500[0], "CE" if r > 0 else "PE", None, None, None, last="15:30")
        trades_day = None
    if strat == "SUPERTR":
        st_line, st_dir = ind.supertrend(df, 10, 3)
        dirn = np.asarray(st_dir, float)
        for i in range(1, len(df)):
            if busy_until is not None and df.index[i] + bar_len <= busy_until:
                continue
            d = df.index[i].date()
            if df.index[i] + bar_len > T(d, "14:30") or df.index[i] + bar_len < T(d, "09:30"):
                continue
            if dirn[i] != dirn[i - 1] and dirn[i] != 0:
                side = "CE" if dirn[i] > 0 else "PE"
                take(i, side, None, None, (lambda j, s=dirn[i]: dirn[j] != s))
    if strat == "NOISE":
        import reversal_study as rv
        Tb = rv.feature_table(key)
        sigma_t = Tb["sigma"]
        o_day = Tb["dopen"]
        pre = bt.precompute(df, key)
        vwap = pre["vwap"].to_numpy(float)
        cl = df["Close"].to_numpy(float)
        prevc = {d: float(prev[d].C) for d in prev}
        marks = set(f"{h:02d}:{m:02d}" for h in range(10, 16) for m in (0, 30) if (h, m) <= (15, 0))
        for i in range(len(df)):
            if busy_until is not None and df.index[i] + bar_len <= busy_until:
                continue
            tc = df.index[i] + bar_len
            d = tc.date()
            if tc.strftime("%H:%M") not in marks or d not in prevc or not (sigma_t[i] == sigma_t[i]):
                continue
            upper = max(o_day[i], prevc[d]) * (1 + sigma_t[i])
            lower = min(o_day[i], prevc[d]) * (1 - sigma_t[i])
            side = "CE" if cl[i] > upper else "PE" if cl[i] < lower else None
            if side is None:
                continue

            def ex(j, side=side, d=d):
                tj = df.index[j] + bar_len
                if tj.strftime("%H:%M") not in marks:
                    return False
                up = max(o_day[j], prevc[d]) * (1 + sigma_t[j])
                lo = min(o_day[j], prevc[d]) * (1 - sigma_t[j])
                return cl[j] < max(up, vwap[j]) if side == "CE" else cl[j] > min(lo, vwap[j])
            take(i, side, None, None, ex)
    return trades


def _job(job):
    key, strat = job
    return job, run(key, strat)


def _tool(job):
    key, system = job
    if system == "rules":
        import order_block_study as ob
        return job, ob.rules_rows(key)
    import tv_ratings_study as tvs
    _, out = tvs._tv_job(key)
    return job, out["tv_ours"]


def main():
    import multiprocessing as mp
    import stop_day_study as sds
    jobs = [(k, s) for s in STRATS for k in INDICES if not (s == "FIRST5" and k not in FIVE)]
    tools = [("NIFTY", "tv"), ("NIFTY", "rules"), ("BANKNIFTY", "tv"), ("SENSEX", "rules"), ("MIDCPNIFTY", "rules")]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
        base = dict(pool.map(_tool, tools, chunksize=1))
    live = {"NIFTY": ("NIFTY", "tv"), "BANKNIFTY": ("BANKNIFTY", "tv"), "SENSEX": ("SENSEX", "rules"),
            "MIDCPNIFTY": ("MIDCPNIFTY", "rules")}
    W = 170
    print("=" * W)
    print(" POPULAR INDIAN INTRADAY STRATEGIES AGAINST THE TOOL - at-the-money options, per lot after costs, ₹  "
          "(columns: trades, won, total, profit factor, worst drop, worst day)")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    better = []
    for k in INDICES:
        print(f" {k}")
        print(sds.line(f"   THE TOOL ({'TradingView rating' if live[k][1] == 'tv' else 'rules'})", base[live[k]]))
        if k == "NIFTY":
            print(sds.line("   (the tool's rules)", base[("NIFTY", "rules")]))
        b = sds.split(base[live[k]])
        for s in STRATS:
            if (k, s) not in got:
                continue
            rows = sorted(got[(k, s)], key=lambda r: r["when"])
            if not rows:
                print(f"   {s:44s} no trades")
                continue
            print(sds.line(f"   {s}", rows))
            r = sds.split(rows)
            if r["is"]["total"] > b["is"]["total"] and r["oos"]["total"] > b["oos"]["total"]:
                better.append(f"{s} on {k}")
    print("\n VERDICT: " + ("BETTER THAN THE TOOL - " + ", ".join(better) if better else
                            "NONE BEATS THE TOOL'S OWN SYSTEM ON ANY INDEX IN BOTH PERIODS"))
    # also: which made money at all in both periods
    pos = [f"{s} on {k}" for (k, s), rows in got.items() if rows and all(sds.split(rows)[p]["total"] > 0 for p in ("is", "oos"))]
    print("   made money in both periods (but less than the tool): " + (", ".join(sorted(pos)) if pos else "none"))
    return got, base


if __name__ == "__main__":
    main()
