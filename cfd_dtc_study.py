#!/usr/bin/env python3
"""
cfd_dtc_study.py — the published core of "DTC Trading Club v1.3.6" on Exness BTC and gold
================================================================================
The user, 4 Oct 2026: "can you look for dtc 1.3.6 indicator" -> "yes run the test". The indicator is paid and
invite-only (dtctradingclub.com); its page publishes the core: six EMAs (30/35/40/45/50/60), a BUY when they come
into full bullish alignment (30 > 35 > 40 > 45 > 50 > 60) from not being aligned, a SELL the mirror; an ATR
volatility filter (period / multiplier not published); a stop and TP1-TP7 at risk-reward multiples (formulas not
published). So this tests the IDEA, not the exact product. Fixed before the run (16 per market and candle size):
  filter   none | ATR(14) above its own 50-candle average
  stop     swing: beyond the last 10 candles' low (buy) / high (sell) | 1.5 x ATR(14)
  exit     one target at 1R | 2R | 3R | the ribbon breaking (EMA30 back through EMA60, at a close)
on 15-minute and 1-hour candles. Real Exness ticks (the stop at the first tick through it, a target a resting
limit), Exness's spread (BTC $10+, gold $0.26+) in and out, its swap on buys, one position at a time, 20 minutes
after an exit, out after 24 hours (the tool's limit). A setup counts only if profitable in BOTH periods and its
held-out beats 1 - 0.05/16 of 300 coin flips (same entries, random direction, same exit).

    python3 cfd_dtc_study.py
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

HOLD_NS = 24 * 3600 * 10 ** 9
STALE_NS = 600 * 10 ** 9
COOLDOWN_NS = 20 * 60 * 10 ** 9
EXITS = ("1R", "2R", "3R", "ribbon")
FLOOR = {"BTCUSD": 10.0, "XAUUSD": 0.26}
MULT = {"BTCUSD": 1.0, "XAUUSD": 100.0}
N_FLIPS = 300
K = 16


def bars(sym, tf):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import exness_data as ed
    full = ed.load(sym).tz_convert("UTC")
    full = full[["Open", "High", "Low", "Close", "Volume", "spread_close"]]
    if tf == "15m":
        return full
    return full.resample("1h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum",
                                    "spread_close": "last"}).dropna()


def indicators(df):
    c, h, l = df["Close"], df["High"], df["Low"]
    e = {n: c.ewm(span=n, adjust=False).mean().to_numpy() for n in (30, 35, 40, 45, 50, 60)}
    bull = (e[30] > e[35]) & (e[35] > e[40]) & (e[40] > e[45]) & (e[45] > e[50]) & (e[50] > e[60])
    bear = (e[30] < e[35]) & (e[35] < e[40]) & (e[40] < e[45]) & (e[45] < e[50]) & (e[50] < e[60])
    side = np.where(bull & ~np.roll(bull, 1), 1, np.where(bear & ~np.roll(bear, 1), -1, 0))
    side[:120] = 0
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    vol_ok = (atr > atr.rolling(50).mean()).to_numpy()
    lo10, hi10 = l.rolling(10).min().to_numpy(), h.rolling(10).max().to_numpy()
    return side, atr.to_numpy(), vol_ok, lo10, hi10, e[30], e[60]


def _walk(args):
    """One month's candidates on real ticks, both directions (for the coin flip): for each, per exit, (net pts, exit ns)."""
    sym, y, m, rows, floor = args
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_tick_study as t
    parts = [p for p in (t._month_ticks(sym, y, m), t._month_ticks(sym, *((y + 1, 1) if m == 12 else (y, m + 1))))
             if p is not None]
    if not parts or not rows:
        return []
    T = np.concatenate([p[0] for p in parts]); M = np.concatenate([p[1] for p in parts])
    S = np.concatenate([p[2] for p in parts])
    out = []
    for key, t0, close, spc, Rs, rib in rows:              # Rs: {side: stop distance}; rib: {side: (ribbon exit ns, its close)}
        last = np.searchsorted(T, t0) - 1
        if last < 0 or t0 - T[last] > STALE_NS:
            continue
        a, b = np.searchsorted(T, t0), np.searchsorted(T, t0 + HOLD_NS)
        if b <= a:
            continue
        res = {}
        for sd in (1, -1):
            R = Rs[sd]
            f = sd * (M[a:b] - close)
            s_ = np.flatnonzero(f <= -R)
            qs = s_[0] if len(s_) else None
            per = {}
            for ex in EXITS:
                if ex == "ribbon":
                    rt, rc = rib[sd]
                    q_r = np.searchsorted(T[a:b], rt) if rt is not None else None
                    if qs is not None and (q_r is None or qs <= q_r):
                        q, pts = qs, f[qs]
                    elif q_r is not None and q_r < len(f):
                        q, pts = q_r, sd * (rc - close)
                    else:
                        q, pts = len(f) - 1, f[-1]
                else:
                    tg = {"1R": 1.0, "2R": 2.0, "3R": 3.0}[ex] * R
                    g = np.flatnonzero(f >= tg)
                    qg = g[0] if len(g) else None
                    if qg is not None and (qs is None or qg < qs):
                        q, pts = qg, tg
                    elif qs is not None:
                        q, pts = qs, f[qs]
                    else:
                        q, pts = len(f) - 1, f[-1]
                per[ex] = (pts - max(spc, floor) / 2 - max(S[a + q], floor) / 2, int(T[a + q]))
            res[sd] = per
        out.append((key, t0, res))
    return out


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_tick_study as cts
    import cfd_vote_search as vs
    import exness_data as ed
    rng = np.random.default_rng(20261004)
    for sym in ("BTCUSD", "XAUUSD"):
        long_sw, _, triple = cts.SWAP[sym]
        for tf in ("15m", "1h"):
            df = bars(sym, tf)
            side, atr, vol_ok, lo10, hi10, e30, e60 = indicators(df)
            bar_ns = (15 if tf == "15m" else 60) * 60 * 10 ** 9
            close_ns = df.index.asi8 + bar_ns
            cv = df["Close"].to_numpy()
            # the ribbon exit for each direction: the first later close where EMA30 is back through EMA60
            cross_dn = np.flatnonzero(e30 < e60)
            cross_up = np.flatnonzero(e30 > e60)
            cand_idx = np.flatnonzero(side != 0)
            for stop in ("swing", "1.5 ATR"):
                by_month = {}
                for i in cand_idx:
                    if stop == "swing":
                        Rs = {1: cv[i] - lo10[i], -1: hi10[i] - cv[i]}
                    else:
                        Rs = {1: 1.5 * atr[i], -1: 1.5 * atr[i]}
                    Rs = {sd: max(r, 0.25 * atr[i]) for sd, r in Rs.items()}      # never a stop inside the noise
                    rib = {}
                    for sd, arr in ((1, cross_dn), (-1, cross_up)):
                        p = np.searchsorted(arr, i + 1)
                        rib[sd] = (int(close_ns[arr[p]]), float(cv[arr[p]])) if p < len(arr) else (None, None)
                    ts_ = pd.Timestamp(int(close_ns[i]), tz="UTC")
                    by_month.setdefault((ts_.year, ts_.month), []).append(
                        (int(i), int(close_ns[i]), float(cv[i]), float(df["spread_close"].iloc[i]), Rs, rib))
                jobs = [(sym, y, m, by_month.get((y, m), []), FLOOR[sym]) for (y, m) in ed.months_until(cts.LAST)]
                walked = []
                with ProcessPoolExecutor(max(1, min(6, (os.cpu_count() or 2) - 1))) as ex:
                    for got in ex.map(_walk, jobs):
                        walked.extend(got)
                walked.sort(key=lambda r: r[1])
                rolls, cumw = vs.rollover_weights(walked[0][1], walked[-1][1] + 4 * 86400 * 10 ** 9, triple)

                def run(exit_, filt, sides=None):
                    out, free = [], -1
                    for n, (i, t0, res) in enumerate(walked):
                        if t0 < free or (filt and not vol_ok[i]):
                            continue
                        sd = side[i] if sides is None else sides[n]
                        pts, t1 = res[sd][exit_]
                        nights = cumw[np.searchsorted(rolls, t1, side="right") - 1] - cumw[np.searchsorted(rolls, t0, side="right") - 1]
                        out.append((t0, (pts + (nights * long_sw / MULT[sym] if sd > 0 else 0.0)) * MULT[sym]))
                        free = t1 + COOLDOWN_NS
                    return out

                if stop == "swing":
                    print(f"\n{'=' * 116}\n {sym} {tf}: {len(walked)} DTC signals (6-EMA alignment flips); $ per {'BTC' if sym == 'BTCUSD' else 'lot (100 oz)'}"
                          f"\n{'=' * 116}")
                    print(f"  {'filter / stop / exit':34s} {'IN-SAMPLE   n  win%      net   PF':>34s}   {'HELD-OUT   n  win%      net   PF':>34s}   {'coin p':>7s}")
                for filt in (False, True):
                    for exit_ in EXITS:
                        tr = run(exit_, filt)
                        cells, nets = [], []
                        for per in ("is", "oos"):
                            x = np.array([v for t0, v in tr if (t0 < vs.SPLIT) == (per == "is")])
                            st = vs.stats(x)
                            nets.append(st["net"])
                            cells.append(f"{st['n']:>5} {100 * st['win']:>4.0f}% {st['net']:>+9,.0f} {st['pf']:>5.2f}")
                        p = None
                        if nets[0] > 0 and nets[1] > 0:
                            flips = []
                            for _ in range(N_FLIPS):
                                sides = rng.choice([-1, 1], len(walked))
                                flips.append(sum(v for t0, v in run(exit_, filt, sides) if t0 >= vs.SPLIT))
                            p = (1 + np.sum(np.array(flips) >= nets[1])) / (1 + N_FLIPS)
                        name = f"{'ATR filter' if filt else 'no filter'} / {stop} / {exit_}"
                        print(f"  {name:34s} {cells[0]:>34s}   {cells[1]:>34s}   {'' if p is None else f'{p:>7.3f}'}"
                              f"{'  PASS' if p is not None and p <= 0.05 / K else ''}", flush=True)


if __name__ == "__main__":
    main()
