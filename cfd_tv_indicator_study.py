#!/usr/bin/env python3
"""
cfd_tv_indicator_study.py — 10 popular open-source TradingView community indicators vs our RSI-2 rule (Exness BTC)
================================================================================
The user, 4 Oct 2026: "check all kind of indicators in the trading view of people who create their own which are
published on trading view for crypto market test all the indicators and pick one and compare with our startegy".
All 100,000+ community scripts cannot be tested (and many are invite-only - their code is hidden); these are the
most-used OPEN-SOURCE ones, ported with their published defaults (tv_indicators.py): UT Bot Alerts, Squeeze
Momentum, WaveTrend, SSL Channel, Chandelier Exit, Range Filter, HalfTrend, Hull Suite, Coral Trend, Supertrend.

Each on BTC 15-minute and 1-hour candles, two ways (fixed before the run):
  own exit   out when the indicator itself says so (its flip / its exit), a 3 x ATR safety stop
  bracket    stop 2 x ATR, one target at 2R
Real Exness ticks (stop at the first tick through it, target a resting limit), $10+ spread in and out, swap on buys,
one position at a time, 20 minutes after an exit, 24 hours at most. A setup counts only if profitable in BOTH
periods and its held-out beats 1 - 0.05/40 of 2,000 coin flips. Scale: the live RSI-2 rule +25,729 / +25,228 per BTC.

    python3 cfd_tv_indicator_study.py
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

HOLD_NS = 24 * 3600 * 10 ** 9
STALE_NS = 600 * 10 ** 9
COOLDOWN_NS = 20 * 60 * 10 ** 9
FLOOR = 10.0
N_FLIPS = 2000
K = 40


def bars(tf):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import exness_data as ed
    full = ed.load("BTCUSD").tz_convert("UTC")[["Open", "High", "Low", "Close", "Volume", "spread_close"]]
    if tf == "15m":
        return full
    return full.resample("1h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum",
                                    "spread_close": "last"}).dropna()


def _walk(args):
    y, m, rows = args
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_tick_study as t
    parts = [p for p in (t._month_ticks("BTCUSD", y, m), t._month_ticks("BTCUSD", *((y + 1, 1) if m == 12 else (y, m + 1))))
             if p is not None]
    if not parts or not rows:
        return []
    T = np.concatenate([p[0] for p in parts]); M = np.concatenate([p[1] for p in parts])
    S = np.concatenate([p[2] for p in parts])
    out = []
    for key, t0, close, spc, atr, own in rows:            # own: {side: (exit close ns, its close) or (None, None)}
        last = np.searchsorted(T, t0) - 1
        if last < 0 or t0 - T[last] > STALE_NS:
            continue
        a, b = np.searchsorted(T, t0), np.searchsorted(T, t0 + HOLD_NS)
        if b <= a:
            continue
        res = {}
        for sd in (1, -1):
            f = sd * (M[a:b] - close)
            per = {}
            # own exit, a 3-ATR safety stop
            R = 3.0 * atr
            s_ = np.flatnonzero(f <= -R); qs = s_[0] if len(s_) else None
            rt, rc = own[sd]
            q_r = np.searchsorted(T[a:b], rt) if rt is not None else None
            if qs is not None and (q_r is None or qs <= q_r):
                q, pts = qs, f[qs]
            elif q_r is not None and q_r < len(f):
                q, pts = q_r, sd * (rc - close)
            else:
                q, pts = len(f) - 1, f[-1]
            per["own exit"] = (pts - max(spc, FLOOR) / 2 - max(S[a + q], FLOOR) / 2, int(T[a + q]))
            # bracket: 2-ATR stop, 2R target
            R = 2.0 * atr
            s_ = np.flatnonzero(f <= -R); qs = s_[0] if len(s_) else None
            g = np.flatnonzero(f >= 2 * R); qg = g[0] if len(g) else None
            if qg is not None and (qs is None or qg < qs):
                q, pts = qg, 2 * R
            elif qs is not None:
                q, pts = qs, f[qs]
            else:
                q, pts = len(f) - 1, f[-1]
            per["bracket 2R"] = (pts - max(spc, FLOOR) / 2 - max(S[a + q], FLOOR) / 2, int(T[a + q]))
            res[sd] = per
        out.append((key, t0, res))
    return out


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_tick_study as cts
    import cfd_vote_search as vs
    import exness_data as ed
    import tv_indicators as tv
    rng = np.random.default_rng(20261004)
    long_sw, _, triple = cts.SWAP["BTCUSD"]
    summary = []
    print("BTC on Exness; $ per BTC; real ticks; the live RSI-2 rule for scale: +25,729 / +25,228 (87% won)")
    print(f"  {'indicator / candles / exit':40s} {'IN-SAMPLE   n  win%      net   PF':>34s}   {'HELD-OUT   n  win%      net   PF':>34s}   {'coin p':>7s}")
    for tf in ("15m", "1h"):
        df = bars(tf)
        bar_ns = (15 if tf == "15m" else 60) * 60 * 10 ** 9
        close_ns = df.index.asi8 + bar_ns
        cv = df["Close"].to_numpy()
        h, l, c = df["High"], df["Low"], df["Close"]
        atr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
        for name, fn in tv.ALL.items():
            entry, xl, xs = fn(df[["Open", "High", "Low", "Close", "Volume"]])
            entry = np.asarray(entry, dtype=int); entry[:300] = 0
            xl_i, xs_i = np.flatnonzero(np.asarray(xl, dtype=bool)), np.flatnonzero(np.asarray(xs, dtype=bool))
            by_month = {}
            for i in np.flatnonzero(entry != 0):
                own = {}
                for sd, arr in ((1, xl_i), (-1, xs_i)):
                    p = np.searchsorted(arr, i + 1)
                    own[sd] = (int(close_ns[arr[p]]), float(cv[arr[p]])) if p < len(arr) else (None, None)
                ts_ = pd.Timestamp(int(close_ns[i]), tz="UTC")
                by_month.setdefault((ts_.year, ts_.month), []).append(
                    (int(i), int(close_ns[i]), float(cv[i]), float(df["spread_close"].iloc[i]), float(atr[i]), own))
            jobs = [(y, m, by_month.get((y, m), [])) for (y, m) in ed.months_until(cts.LAST)]
            walked = []
            with ProcessPoolExecutor(max(1, min(6, (os.cpu_count() or 2) - 1))) as ex:
                for got in ex.map(_walk, jobs):
                    walked.extend(got)
            walked.sort(key=lambda r: r[1])
            rolls, cumw = vs.rollover_weights(walked[0][1], walked[-1][1] + 4 * 86400 * 10 ** 9, triple)
            t0s = np.array([w[1] for w in walked])

            def run(exit_, sides):
                out, free = [], -1
                for n, (i, t0, res) in enumerate(walked):
                    if t0 < free:
                        continue
                    sd = int(sides[n])
                    pts, t1 = res[sd][exit_]
                    nights = cumw[np.searchsorted(rolls, t1, side="right") - 1] - cumw[np.searchsorted(rolls, t0, side="right") - 1]
                    out.append((t0, pts + (nights * long_sw if sd > 0 else 0.0)))
                    free = t1 + COOLDOWN_NS
                return out

            real = np.array([entry[w[0]] for w in walked])
            for exit_ in ("own exit", "bracket 2R"):
                tr = run(exit_, real)
                cells, nets = [], []
                for per in ("is", "oos"):
                    x = np.array([v for t0, v in tr if (t0 < vs.SPLIT) == (per == "is")])
                    st = vs.stats(x)
                    nets.append(st["net"])
                    cells.append(f"{st['n']:>5} {100 * st['win']:>4.0f}% {st['net']:>+9,.0f} {st['pf']:>5.2f}")
                p = None
                if nets[0] > 0 and nets[1] > 0:
                    flips = [sum(v for t0, v in run(exit_, rng.choice([-1, 1], len(walked))) if t0 >= vs.SPLIT)
                             for _ in range(N_FLIPS)]
                    p = (1 + np.sum(np.array(flips) >= nets[1])) / (1 + N_FLIPS)
                label = f"{name} / {tf} / {exit_}"
                print(f"  {label:40s} {cells[0]:>34s}   {cells[1]:>34s}   {'' if p is None else f'{p:>7.4f}'}"
                      f"{'  PASS' if p is not None and p <= 0.05 / K else ''}", flush=True)
                summary.append((label, nets[0], nets[1], p))
    both = [s for s in summary if s[1] > 0 and s[2] > 0]
    print(f"\n  profitable in both periods: {len(both)} of {len(summary)}"
          + ("".join(f"\n    {s[0]}: {s[1]:+,.0f} / {s[2]:+,.0f}, coin p {s[3]:.4f}" for s in sorted(both, key=lambda s: -(s[1] + s[2]))) if both else ""))
    print(f"  pass the strict test: {sum(1 for s in summary if s[3] is not None and s[3] <= 0.05 / K)}")
    print("  the live RSI-2 rule: +25,729 / +25,228 per BTC, 87% won, coin flips as good ~1%")


if __name__ == "__main__":
    main()
