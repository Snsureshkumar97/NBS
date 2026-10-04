#!/usr/bin/env python3
"""
cfd_target_sweep.py — the 87% BTC rule's reward:risk from 0.1 to 0.9 (stop 3 x ATR), on real ticks
================================================================================
The user, 4 Oct 2026: "risk reward is too low can we change to 1:1" -> 1:1 made less money with deeper drops ->
"check from 0.1 to 0.9". Every signal of the live rule (RSI-2 + ADX >= 25) walked on Exness's ticks ONCE, every
target read off the same path (the target counts only if reached before the stop); the spread check applied per
target (no trade when the spread is 20%+ of THAT target); $10+ spread in and out, swap on buys, one at a time,
20 minutes after an exit, 24 hours at most. 0.2 must reproduce the live result (cfd_timeframe_study: 15m).

    python3 cfd_target_sweep.py
    python3 cfd_target_sweep.py --ladder     exits at the card's T1 / T2 / T3 (1/3, 2/3, all of the 0.2 x stop target)
                                             - the user, 4 Oct 2026: "what will be the result if we make t2 as exit"
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

TARGETS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
LADDER = (0.2 / 3, 0.4 / 3, 0.2)


def _label(r):
    if r in LADDER and TARGETS == LADDER:
        return {0: "exit at T1", 1: "exit at T2", 2: "T3 (live)"}[LADDER.index(r)]
    return f"{r:.1f} : 1" + ("  (live)" if r == 0.2 else "")
STOP_ATR = 3.0
FLOOR = 10.0
HOLD_NS = 24 * 3600 * 10 ** 9
STALE_NS = 600 * 10 ** 9
COOLDOWN_NS = 20 * 60 * 10 ** 9


def _walk(args):
    y, m, rows, targets = args                    # the targets travel WITH the job: a worker process imports this module
                                                  # afresh and would otherwise walk the default TARGETS
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_tick_study as t
    parts = [p for p in (t._month_ticks("BTCUSD", y, m), t._month_ticks("BTCUSD", *((y + 1, 1) if m == 12 else (y, m + 1))))
             if p is not None]
    if not parts or not rows:
        return []
    T = np.concatenate([p[0] for p in parts]); M = np.concatenate([p[1] for p in parts])
    S = np.concatenate([p[2] for p in parts])
    tg = np.array(targets)
    out = []
    for t0, side, close, R, spc in rows:
        last = np.searchsorted(T, t0) - 1
        if last < 0 or t0 - T[last] > STALE_NS:
            continue
        a, b = np.searchsorted(T, t0), np.searchsorted(T, t0 + HOLD_NS)
        if b <= a:
            continue
        f = side * (M[a:b] - close)
        s_ = np.flatnonzero(f <= -R)
        qs = s_[0] if len(s_) else None
        upto = f[:qs] if qs is not None else f
        firsts = np.searchsorted(np.maximum.accumulate(upto), tg * R) if len(upto) else np.full(len(tg), 0)
        res = []
        for jj in range(len(tg)):
            if len(upto) and firsts[jj] < len(upto):
                q, pts = firsts[jj], tg[jj] * R
            elif qs is not None:
                q, pts = qs, f[qs]
            else:
                q, pts = len(f) - 1, f[-1]
            res.append((pts - max(spc, FLOOR) / 2 - max(S[a + q], FLOOR) / 2, int(T[a + q])))
        out.append((t0, side, R, spc, res))
    return out


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_rules
    import cfd_tick_study as cts
    import cfd_vote_search as vs
    import exness_data as ed
    full = ed.load("BTCUSD")
    a = cfd_rules.compute(full[["Open", "High", "Low", "Close", "Volume"]])
    side = np.where((a["rsi2"] != 0) & a["adx25"], a["rsi2"], 0)
    close_ns = full.index.tz_convert("UTC").asi8 + 15 * 60 * 10 ** 9
    by_month = {}
    for i in np.flatnonzero(side != 0):
        ts = pd.Timestamp(int(close_ns[i]), tz="UTC")
        by_month.setdefault((ts.year, ts.month), []).append(
            (int(close_ns[i]), int(side[i]), float(full["Close"].iloc[i]), float(STOP_ATR * a["atr"][i]),
             float(full["spread_close"].iloc[i])))
    jobs = [(y, m, by_month.get((y, m), []), TARGETS) for (y, m) in ed.months_until(cts.LAST)]
    cands = []
    with ProcessPoolExecutor(max(1, min(6, (os.cpu_count() or 2) - 1))) as ex:
        for got in ex.map(_walk, jobs):
            cands.extend(got)
    cands.sort(key=lambda r: r[0])
    long_sw, _, triple = cts.SWAP["BTCUSD"]
    rolls, cumw = vs.rollover_weights(cands[0][0], cands[-1][4][0][1] + 3 * 86400 * 10 ** 9, triple)
    print("BTC, the live rule (RSI-2 + ADX >= 25), stop 3 x ATR, ONE target at r x the stop; $ per BTC; real ticks.")
    print(f"  {'reward:risk':12s} {'IN-SAMPLE   n   win%      net    PF  maxDD':>44s}   {'HELD-OUT   n   win%      net    PF  maxDD':>44s}   "
          f"{'0.25 lot 3y':>11s} {'worst drop 0.25 lot':>20s}")
    for jj, r in enumerate(TARGETS):
        taken, free = [], -1
        for t0, s, R, spc, res in cands:
            if t0 < free or max(spc, FLOOR) >= 0.2 * r * R:              # one at a time; the spread check on THIS target
                continue
            pts, t1 = res[jj]
            nights = cumw[np.searchsorted(rolls, t1, side="right") - 1] - cumw[np.searchsorted(rolls, t0, side="right") - 1]
            taken.append((t0, pts + (nights * long_sw if s > 0 else 0.0)))
            free = t1 + COOLDOWN_NS
        cells, tot = [], 0.0
        for per in ("is", "oos"):
            x = np.array([v for t0, v in taken if (t0 < vs.SPLIT) == (per == "is")])
            st = vs.stats(x)
            tot += st["net"]
            cells.append(f"{st['n']:>5} {100 * st['win']:>5.1f}% {st['net']:>+9,.0f} {st['pf']:>5.2f} {st['dd']:>6,.0f}")
        eq = np.cumsum([0.25 * v for _, v in taken])
        dd = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq)) if len(eq) else 0.0
        print(f"  {_label(r):12s} {cells[0]:>44s}   {cells[1]:>44s}   "
              f"{0.25 * tot:>+11,.0f} {dd:>19,.0f}", flush=True)


if __name__ == "__main__":
    if "--ladder" in sys.argv:
        TARGETS = LADDER
    main()
