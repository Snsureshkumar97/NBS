#!/usr/bin/env python3
"""
cfd_outcomes.py — every 15-minute bar's trade outcome on Exness's REAL ticks, both sides
================================================================================
For cfd_vote_search.py (the user, 4 Oct 2026: "check all the startegies in the world and check
what votes makes profitable improve win rate ... mix and match ... bring out the best one").
Testing thousands of entry combinations on ticks one by one would take days; but a bracket
trade's outcome depends only on WHEN it enters, WHICH side, and WHERE its stop and target sit -
not on why it entered. So it is worked out ONCE here, for every bar, both sides, every stop and
every target, and any entry rule afterwards is a lookup.

THE TRADE: entry at the bar's close (the mid, as live), stop at m x ATR(14, 15m) from it,
target at t x that distance (a resting limit, filled at the target), out at the market after
24 hours. Walked tick by tick from the next bar's open: the stop fills at the first tick
through it (its own price), the target at the first tick that reaches it - whichever comes
first. Spread: half the entry bar's closing spread in, half the exit tick's spread out. Swap
is added later (it depends on the exit time, kept here).

Bars without a live price (the last tick more than CFD_STALE_QUOTE_S before the close - gold
at the weekend) get no trade, as live (tickets._closed_hold).

STOPS (x ATR): 1, 1.5, 2, 3.  TARGETS (x the stop distance): 0.5, 0.75, 1, 1.5, 2, 3, 5.

Output, per symbol: <exness cache>/outcomes_<SYM>.npz - net points [bar, side, stop, target]
(side 0 = buy, 1 = sell), exit time (ns UTC), and the bar index / ATR / tradable flag.

    python3 cfd_outcomes.py
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

STOPS = (1.0, 1.5, 2.0, 3.0)
TARGETS = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0)
HOLD_NS = 24 * 3600 * 10 ** 9
BAR_NS = 15 * 60 * 10 ** 9
STALE_NS = 600 * 10 ** 9


def atr14(df):
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - df["Close"].shift()).abs(),
                    (df["Low"] - df["Close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / 14, adjust=False).mean()


def _month(args):
    """Outcomes for the bars whose ENTRY falls in one month (ticks of that month + the next)."""
    sym, y, m = args
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_tick_study as t
    import exness_data as ed
    full = ed.load(sym)
    bar_start = full.index.tz_convert("UTC").asi8
    cl = full["Close"].to_numpy()
    spc = full["spread_close"].to_numpy()
    atr = atr14(full).to_numpy()
    lo = pd.Timestamp(f"{y}-{m:02d}-01", tz="UTC").value
    hi = (pd.Timestamp(f"{y}-{m:02d}-01", tz="UTC") + pd.offsets.MonthBegin(1)).value
    idx = np.flatnonzero((bar_start + BAR_NS >= lo) & (bar_start + BAR_NS < hi))
    parts = [p for p in (t._month_ticks(sym, y, m), t._month_ticks(sym, *((y + 1, 1) if m == 12 else (y, m + 1))))
             if p is not None]
    S_, M_, T_ = len(STOPS), len(TARGETS), len(idx)
    net = np.full((T_, 2, S_, M_), np.nan, dtype=np.float32)
    ext = np.zeros((T_, 2, S_, M_), dtype=np.int64)
    ok = np.zeros(T_, dtype=bool)
    if not parts or not T_:
        return sym, idx, net, ext, ok
    T = np.concatenate([p[0] for p in parts]); M = np.concatenate([p[1] for p in parts])
    SP = np.concatenate([p[2] for p in parts])
    tg = np.array(TARGETS)
    for n, i in enumerate(idx):
        t0 = bar_start[i] + BAR_NS                         # the bar's close = the entry time
        last = np.searchsorted(T, t0) - 1                  # the last tick at or before the close
        if last < 0 or t0 - T[last] > STALE_NS or not (atr[i] > 0):
            continue
        a, b = np.searchsorted(T, t0), np.searchsorted(T, t0 + HOLD_NS)
        if b <= a:
            continue
        ok[n] = True
        path = M[a:b] - cl[i]                              # favourable for a buy; minus for a sell
        for sd, sign in ((0, 1.0), (1, -1.0)):
            f = sign * path
            for k, mult in enumerate(STOPS):
                R = mult * atr[i]
                hit_s = np.flatnonzero(f <= -R)
                s = hit_s[0] if len(hit_s) else None
                # the best excursion BEFORE the stop: a target counts only if reached first
                upto = f[:s] if s is not None else f
                if len(upto):
                    run = np.maximum.accumulate(upto)
                    firsts = np.searchsorted(run, tg * R)     # first index where the running max reaches t*R
                else:
                    firsts = np.full(len(tg), 0)
                for j in range(len(tg)):
                    if len(upto) and firsts[j] < len(upto):
                        q = firsts[j]
                        pts, when, xs = tg[j] * R, T[a + q], SP[a + q]
                    elif s is not None:
                        pts, when, xs = f[s], T[a + s], SP[a + s]
                    else:
                        pts, when, xs = f[-1], T[b - 1], SP[b - 1]
                    net[n, sd, k, j] = pts - spc[i] / 2 - xs / 2
                    ext[n, sd, k, j] = when
    return sym, idx, net, ext, ok


def build(sym):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_tick_study as t
    import exness_data as ed
    full = ed.load(sym)
    n = len(full)
    NET = np.full((n, 2, len(STOPS), len(TARGETS)), np.nan, dtype=np.float32)
    EXT = np.zeros((n, 2, len(STOPS), len(TARGETS)), dtype=np.int64)
    OK = np.zeros(n, dtype=bool)
    with ProcessPoolExecutor(max(1, min(6, (os.cpu_count() or 2) - 1))) as ex:
        for s_, idx, net, ext, ok in ex.map(_month, [(sym, y, m) for (y, m) in ed.months_until(t.LAST)]):
            NET[idx], EXT[idx], OK[idx] = net, ext, ok
            print(f"  {sym}: {len(idx)} bars of a month done", flush=True)
    path = os.path.join(ed._dir(), f"outcomes_{sym}.npz")
    np.savez(path, net=NET, ext=EXT, ok=OK, atr=atr14(full).to_numpy(), stops=np.array(STOPS),
             targets=np.array(TARGETS), bar_start=full.index.tz_convert("UTC").asi8)
    return path


if __name__ == "__main__":
    for sym in ("BTCUSD", "XAUUSD"):
        print(sym, "->", build(sym), flush=True)
