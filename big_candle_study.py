#!/usr/bin/env python3
"""
big_candle_study.py — enter on the day's first big 15-minute candle, in its direction
================================================================================
The user, 26 Oct 2026, after a day the tool sat out: Nifty jumped 175 points in ONE 15-minute candle (09:45-10:00) and
then went sideways for four hours; the TradingView rating never reached Strong Buy and the rules' ADX stayed under 20
until 14:15. "can we test entering on the first big candle breakout".

THE RULE, FIXED BEFORE ANY RESULT WAS SEEN (26 Oct 2026):
  a BIG candle is a 15-minute candle whose body (|close - open|) is at least K x the 15-minute ATR(14) as it stood at
  the candle before; a BREAKOUT is one that also closes beyond the day's high (a green candle) or low (a red one) made
  before it. The day's first such candle, from the 09:45 close (the first candle is the opening range, never used) to
  the 14:30 close, is bought at its close in its direction - a call on a green one, a put on a red one. ONE a day.
  PRIMARY      K 2.0, a breakout, stop at the candle's middle, target twice the risk
  SENSITIVITY  K 1.5 / 2.0 / 2.5  x  stop at the middle / the candle's other end  x  target 2 x risk / held to 15:15
               (all breakouts), and the primary without the breakout condition - shown, so one lucky setting cannot
               pass for a rule
On the same footing as every strategy study (indian_strategies_study.py): the at-the-money option of the real expiry
priced as pro_study.price does (Black-Scholes on the day's sigma, slippage 0.25%, Zerodha's charges), per lot at
today's lot sizes, out by 15:15. In-sample to 15 Aug 2025, then held out. Candles: the 3-year caches, extended to
26 Oct 2026 for Nifty, Bank Nifty and Sensex when run with HOME at the recorder-stitched history (the scratch copy
made for the 26 Oct comparison); Midcap's ends 11 Sep 2026.

BETTER only if it earns more than the system the index runs - against BOTH of Nifty's and Bank Nifty's choices (the
TradingView rating, and the rules the user moved them to on 26 Oct), the rules on Sensex and Midcap - in BOTH periods,
and then holds up (robustness.py's walk-forward and Monte Carlo, and again at double slippage). Also shown: the tool
with the big-candle entry added on the days and hours it holds nothing (its own trades first).

RESULT (26 Oct 2026; big_candle_study_results.txt) - THE PRIMARY RULE LOSES TO THE TOOL EVERYWHERE. Its stop at the
candle's middle is too tight: on Nifty +42,051 / +2,406 against the rules' +60,462 / +54,059 (and the TradingView
rating's +267,670 / +158,560), Bank Nifty +881 / -2,055, Sensex +47,478 / +1,280, Midcap -26,845 / +3,057. It even lost
on 26 Oct itself: the 10:00 call was stopped at 11:45 when the pull-back reached the candle's middle. In the sensitivity
grid the stop at the candle's OTHER END does far better (K1.5: Nifty +162,129 / +34,297, Sensex +145,105 / +61,049,
Bank Nifty +84,930 / +53,725) - found by looking, so it went on to big_candle_robustness.py.

    /usr/bin/python3 big_candle_study.py
"""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
PRIMARY = ("K2.0", 2.0, True, "mid", "2R")
VARIANTS = [PRIMARY, ("K2.0 any", 2.0, False, "mid", "2R")] + [
    (f"K{k} {st} {tg}", k, True, st, tg) for k in (1.5, 2.0, 2.5) for st in ("mid", "end") for tg in ("2R", "day")
    if (k, st, tg) != (2.0, "mid", "2R")]


def run(key, variant, slip=0.0025):
    """[{when, out, net, index, side}] - the day's first big candle, per the variant."""
    import backtest_intraday as bt
    import index_candidates_study as ics
    import indian_strategies_study as iss
    import indicators as ind
    import pro_study as ps
    import regime_study as rs
    name, K, breakout, stop_at, target = variant
    ps.SLIP = slip
    if key == "MIDCPNIFTY":
        real = ps._scheduled
        ps._scheduled = lambda k, d, real=real: ics._monthly(d) if k == key else real(k, d)
    df = bt.fetch_history(key, years=3, use_cache=True)
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    sig = rs.features(df, rs.load_vix(), nrv)["sigma"]
    sig_day = sig.groupby(sig.index.date).first().to_dict()
    days = set(df.index.date)
    bar = pd.Timedelta(minutes=15)
    o, h, l, c = (df[x].to_numpy(float) for x in ("Open", "High", "Low", "Close"))
    atr = ind.atr(df, 14).to_numpy(float)
    day_of = np.array([t.date() for t in df.index])
    starts = np.r_[0, np.where(day_of[1:] != day_of[:-1])[0] + 1]
    trades = []
    for n, s0 in enumerate(starts):
        e0 = starts[n + 1] if n + 1 < len(starts) else len(df)
        d = day_of[s0]
        s = sig_day.get(d)
        if s is None or not (s == s) or s <= 0:
            continue
        for i in range(s0 + 1, e0):
            close_at = df.index[i] + bar
            if close_at > iss.T(d, "14:30"):
                break
            a = atr[i - 1]
            if not (a == a) or a <= 0 or abs(c[i] - o[i]) < K * a:
                continue
            side = "CE" if c[i] > o[i] else "PE"
            if breakout:
                hi_before, lo_before = h[s0:i].max(), l[s0:i].min()
                if (side == "CE" and c[i] <= hi_before) or (side == "PE" and c[i] >= lo_before):
                    continue
            mid = (h[i] + l[i]) / 2
            stop = (mid if stop_at == "mid" else l[i]) if side == "CE" else (mid if stop_at == "mid" else h[i])
            risk = (c[i] - stop) if side == "CE" else (stop - c[i])
            if risk <= 0:
                break
            tgt = (c[i] + 2 * risk if side == "CE" else c[i] - 2 * risk) if target == "2R" else None
            px_out, j = iss.walk(df, i, side, stop, tgt)
            out = df.index[j] + bar
            net = iss.opt_net(key, days, close_at, out, c[i], px_out, side, float(s))
            if net is not None:
                trades.append({"when": close_at, "out": out, "net": net, "index": key, "side": side})
            break                                   # one a day: the first big candle, taken or not
    return trades


def with_tool(tool_rows, extra):
    """The tool's own trades, plus the big-candle trades that fall where the tool holds nothing."""
    spans = [(pd.Timestamp(r["when"]), pd.Timestamp(r.get("exit_time") or r["when"])) for r in tool_rows]
    keep = [r for r in extra if not any(a <= r["when"] <= b or (r["when"] <= a <= r["out"]) for a, b in spans)]
    return sorted(list(tool_rows) + keep, key=lambda r: pd.Timestamp(r["when"])), len(keep)


def _job(job):
    key, variant, slip = job
    return job, run(key, variant, slip)


def main():
    import multiprocessing as mp
    import indian_strategies_study as iss
    import stop_day_study as sds
    jobs = [(k, v, 0.0025) for k in INDICES for v in VARIANTS] + [(k, PRIMARY, 0.005) for k in INDICES]
    tools = [(k, s) for k in INDICES for s in ("rules", "tv") if s == "rules" or k in ("NIFTY", "BANKNIFTY")]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
        base = dict(pool.map(iss._tool, tools, chunksize=1))
    W = 170
    print("=" * W)
    print(" THE DAY'S FIRST BIG 15-MINUTE CANDLE, BOUGHT IN ITS DIRECTION - at-the-money options, per lot after costs, ₹  "
          "(columns: trades, won, total, profit factor, worst drop, worst day)")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT")
    verdict = []
    for k in INDICES:
        print(f" {k}")
        systems = [("rules", "the tool's rules")] + ([("tv", "the TradingView rating")] if k in ("NIFTY", "BANKNIFTY") else [])
        for s, label in systems:
            print(sds.line(f"   {label.upper()}", base[(k, s)]))
        for v in VARIANTS:
            rows = sorted(got[(k, v, 0.0025)], key=lambda r: r["when"])
            print(sds.line(f"   {v[0]}{'  <- PRIMARY' if v == PRIMARY else ''}", rows) if rows else f"   {v[0]:44s} no trades")
        p = sorted(got[(k, PRIMARY, 0.0025)], key=lambda r: r["when"])
        p2 = sorted(got[(k, PRIMARY, 0.005)], key=lambda r: r["when"])
        print(sds.line("   PRIMARY at double slippage (0.5%)", p2))
        r = sds.split(p)
        beats = all(r[q]["total"] > sds.split(base[(k, s)])[q]["total"] for s, _ in systems for q in ("is", "oos"))
        for s, label in systems:
            print("   " + sds.verdict(f"PRIMARY vs {label}", p, base[(k, s)]).strip().replace("\n", "\n   "))
            both, added = with_tool(base[(k, s)], p)
            print(sds.line(f"   {label} + big candle when flat (+{added})", both))
            print("   " + sds.verdict(f"{label} + big candle vs {label}", both, base[(k, s)]).strip().split("\n")[0])
        if beats:
            verdict.append(k)
    print("\n VERDICT: " + (f"THE PRIMARY RULE EARNS MORE THAN THE TOOL IN BOTH PERIODS ON {', '.join(verdict)} - see the "
                            "robustness lines" if verdict else "THE PRIMARY RULE DOES NOT BEAT THE TOOL'S SYSTEM ON ANY INDEX IN BOTH PERIODS"))
    # the days the user pointed at
    for k in ("NIFTY", "BANKNIFTY", "SENSEX"):
        recent = [r for r in got[(k, PRIMARY, 0.0025)] if str(r["when"])[:10] >= "2026-10-19"]
        for r in recent:
            print(f"   recent {k}: {r['when']:%d %b %H:%M} {r['side']} -> out {r['out']:%H:%M}, {r['net']:+,.0f}")


if __name__ == "__main__":
    main()
