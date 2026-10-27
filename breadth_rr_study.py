#!/usr/bin/env python3
"""
breadth_rr_study.py — trading on breadth alone: enough of the index's stocks moving one way, a target bigger than the risk
================================================================================
The user, 27 Oct 2026, after breadth and the votes run on each stock failed as a FILTER on the tool's trades
(stock_votes_study.py): "okay just shall we test just by the amount of stock going up or down and entering a trade with
risk : reward more than 1:1". So breadth here is the whole strategy, not a condition on another one.

DATA: as stock_votes_study - the 15-minute candles of the index's own stocks (market_map.CONSTITUENTS, today's member
lists), against the indices' cached three years (18 Sep 2023 - 11 Sep 2026); Nifty, Bank Nifty, Sensex (Midcap Select
has no member list). Breadth at a candle's close is stock_votes_study.stock_reads' "day": the share of the stocks with a
candle there whose close is above their previous day's close (up), or below it (down) - only what was known then.

FIXED BEFORE ANY RESULT WAS SEEN (27 Oct 2026):
  ENTRY   the day's first 15-minute close, from 09:30 to 14:30, with at least T of the stocks up on the day (buy the CE)
          or at least T down (buy the PE); one trade a day, taken or not; T = 60%, 70%, 80%
  STOP    1 x ATR(14) of the index's 15-minute candles from the entry close
  TARGET  1.5 x or 2 x that risk (reward more than risk, as asked); else out at the 15:15 close
  PRICED  at the money on the real expiry, Black-Scholes at the day's volatility (indian_strategies_study.opt_net, the
          big candle's own), 0.25% slippage, every charge, per lot
Six versions (three T x two targets). Two questions:
  ON ITS OWN   does it make money after costs in both the first two years (to 15 Aug 2025) and the held-out year?
  AS AN ADDITION   to today's system (the rules + the big candle, order_block_site_study.slot) on the days and hours it
          holds nothing - the way the big candle was added: kept only if more than today's in BOTH periods, all three
          indices pooled and then on each index on its own (stop_day_study.verdict, with walk-forward and Monte Carlo)
Six versions are tried, so one passing by luck is possible - a pass that does not hold on each index is read as luck.

RESULT (27 Oct 2026; breadth_rr_study_results.txt) - DROP, NOTHING BUILT.
  ON ITS OWN, all three pooled: every version loses in the held-out year (-19,748 to -73,071 per lot; PF 0.86-0.97),
    against today's system +529,169 / +186,262 (PF 1.32 / 1.20). It wins 34-41% (stops 1.4-2.3x as often as targets).
    Bank Nifty loses in every version and period. Profit in both periods only on Sensex at 60% / 70% (+15k-42k / +5k-20k,
    PF 1.02-1.11) and Nifty at 70% (2R: +69,007 / +31,565, PF 1.22 / 1.16; 1.5R: +40,170 / +4,392).
  AS AN ADDITION where the tool holds nothing: no version passes pooled (first two years -48k to +15k, held-out -27k to
    +5k, deeper drawdowns). Per index three of eighteen pass - + T60 2R Sensex (+32,844 / +4,448), + T70 1.5R Sensex
    (+24,866 / +168), + T70 2R Nifty (+11,239 / +28,882) - every one FRAGILE (beats today's in 3-4 of 7 half-years, deeper
    95% drawdown): about what luck gives from eighteen tries.

    /usr/bin/python3 breadth_rr_study.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")
LEVELS = (0.6, 0.7, 0.8)
TARGETS = (1.5, 2.0)
VARIANTS = tuple(f"T{int(t * 100)} {k}R" for t in LEVELS for k in TARGETS)


def run(key):
    """{variant: [{when, out, net, index, side, share}]} - the day's first breadth signal at each level, each target."""
    import backtest_intraday as bt
    import indian_strategies_study as iss
    import indicators as ind
    import pro_study as ps
    import regime_study as rs
    import stock_votes_study as svs
    ps.SLIP = 0.0025
    df = bt.fetch_history(key, years=3, use_cache=True)
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    sig = rs.features(df, rs.load_vix(), nrv)["sigma"]
    sig_day = sig.groupby(sig.index.date).first().to_dict()
    R = svs.stock_reads(df, key)["day"]
    up, dn = R["up"], R["dn"]
    days = set(df.index.date)
    bar = pd.Timedelta(minutes=15)
    c = df["Close"].to_numpy(float)
    atr = ind.atr(df, 14).to_numpy(float)
    day_of = np.array([t.date() for t in df.index])
    starts = np.r_[0, np.where(day_of[1:] != day_of[:-1])[0] + 1]
    out = {v: [] for v in VARIANTS}
    for n, s0 in enumerate(starts):
        e0 = starts[n + 1] if n + 1 < len(starts) else len(df)
        d = day_of[s0]
        s = sig_day.get(d)
        if s is None or not (s == s) or s <= 0:
            continue
        for t in LEVELS:
            for i in range(s0, e0):
                close_at = df.index[i] + bar
                if close_at > iss.T(d, "14:30"):
                    break
                a = atr[i]
                if not (a == a) or a <= 0 or not (up[i] == up[i]):
                    continue
                side = "CE" if up[i] >= t else "PE" if dn[i] >= t else None
                if side is None:
                    continue
                stop = c[i] - a if side == "CE" else c[i] + a
                for k in TARGETS:
                    tgt = c[i] + k * a if side == "CE" else c[i] - k * a
                    px_out, j = iss.walk(df, i, side, stop, tgt)
                    net = iss.opt_net(key, days, close_at, df.index[j] + bar, c[i], px_out, side, float(s))
                    if net is not None:
                        out[f"T{int(t * 100)} {k}R"].append({"when": close_at, "out": df.index[j] + bar, "net": net,
                                                            "index": key, "side": side,
                                                            "share": float(up[i] if side == "CE" else dn[i]),
                                                            "how": "stop" if px_out == stop else "target" if px_out == tgt
                                                            else "close"})
                break                               # one a day at this level: the first signal, taken or not
    return out


def _job(job):
    key, what = job
    if what == "breadth":
        return job, run(key)
    if what == "rules":
        import order_block_study as ob
        return job, ob.rules_rows(key)
    import big_candle_study as bcs
    return job, bcs.run(key, ("K1.5 end 2R", 1.5, True, "end", "2R"))


def main():
    import multiprocessing as mp
    import order_block_site_study as obs
    import stop_day_study as sds
    jobs = [(k, w) for k in INDICES for w in ("breadth", "rules", "bc")]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    s_ = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    today = {k: obs.slot(s_(got[(k, "rules")]), s_(got[(k, "bc")])) for k in INDICES}
    plus = {(k, v): obs.slot(s_(got[(k, "rules")]), s_(got[(k, "bc")] + got[(k, "breadth")][v]))
            for k in INDICES for v in VARIANTS}
    alone = {(k, v): s_(got[(k, "breadth")][v]) for k in INDICES for v in VARIANTS}
    pool_ = lambda d, v=None: s_([r for k in INDICES for r in (d[k] if v is None else d[(k, v)])])
    W = 172
    print("=" * W)
    print(" BREADTH ALONE - the day's first close with T of the index's stocks up (buy CE) or down (buy PE), stop 1 ATR, target"
          " 1.5R / 2R. Per lot, after costs.  columns: trades, won, total, PF, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print(" ON ITS OWN - all three pooled")
    print(sds.line("   today's system (rules + big candle)", pool_(today)))
    for v in VARIANTS:
        print(sds.line("   " + v, pool_(alone, v)))
    for k in INDICES:
        print(f" {k} - on its own")
        print(sds.line("   today's system (rules + big candle)", today[k]))
        for v in VARIANTS:
            rows = alone[(k, v)]
            print(sds.line("   " + v, rows) if rows else f"   {v:44s} no trades")
    print("\n HOW THEY CLOSED (on its own, all three pooled)")
    for v in VARIANTS:
        print(f"   {v:10s} {pd.Series([r['how'] for r in pool_(alone, v)]).value_counts().to_dict()}")
    print("\n AS AN ADDITION to today's system, where it holds nothing - all three pooled")
    print(sds.line("   today's system", pool_(today)))
    for v in VARIANTS:
        print(sds.line("   + " + v, pool_(plus, v)))
    print("\n VERDICT - the addition kept only if more than today's in BOTH periods (all three pooled), and then on each index")
    for v in VARIANTS:
        print("  " + sds.verdict("+ " + v, pool_(plus, v), pool_(today)).strip())
        for k in INDICES:
            print("      " + sds.verdict(f"+ {v} {k}", plus[(k, v)], today[k]).strip())
    return got


if __name__ == "__main__":
    main()
