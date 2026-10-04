#!/usr/bin/env python3
"""
cfd_timeframe_study.py — the 87% BTC rule on OTHER candle sizes: is 15 minutes special?
================================================================================
The user, 4 Oct 2026: "why rsi extreme 15 minutes why not other minutes". The search that found the rule
only ever used 15-minute candles. The SAME rule on 5-minute, 30-minute, 1-hour and 4-hour candles - nothing
re-tuned - says whether it is an effect of the market (it should hold on neighbouring sizes) or a quirk of
one candle size (a warning). 1-minute is left out: Exness's $10 spread is ~55% of the target there.

THE RULE, per candle size: RSI-2 below 10 above the 200-candle average = buy, above 90 below it = sell; ADX(14)
>= 25; the stop 3 x ATR(14) of that candle size, ONE target 0.2 x the stop; no trade when the spread is 20%+
of the target. Entered at the candle's close; walked on Exness's REAL ticks (the stop at the first tick through
it, the target a resting limit), out after 24 hours; Exness's spread (at least $10) in and out, swap on buys;
one position at a time, 20 minutes after an exit. 5-minute candles are built from the ticks; the longer ones
from the 15-minute candles (which are built from the same ticks). The 15-minute run must reproduce the
live rule's known result (cfd_monthly_live_rules.py).

    python3 cfd_timeframe_study.py
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

FLOOR = 10.0
HOLD_NS = 24 * 3600 * 10 ** 9
STALE_NS = 600 * 10 ** 9
COOLDOWN_NS = 20 * 60 * 10 ** 9


def _rma(s, n):
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def bars_5m():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_tick_study as t
    import exness_data as ed
    path = os.path.join(ed._dir(), "BTCUSD_5m_from_ticks.pkl")
    if os.path.exists(path):
        return pd.read_pickle(path)
    parts = []
    for (y, m) in ed.months_until(t.LAST):
        got = t._month_ticks("BTCUSD", y, m)
        if got is None:
            continue
        T, M, S = got
        idx = pd.to_datetime(T, utc=True)
        mid, spr = pd.Series(M, index=idx), pd.Series(S, index=idx)
        o = mid.resample("5min").ohlc()
        o["volume"] = mid.resample("5min").count()
        o["spread_close"] = spr.resample("5min").last()
        parts.append(o.dropna())
    df = pd.concat(parts)
    df = df[~df.index.duplicated()]
    df.columns = ["Open", "High", "Low", "Close", "Volume", "spread_close"]
    df.to_pickle(path)
    return df


def bars(tf):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import exness_data as ed
    if tf == "5m":
        return bars_5m()
    full = ed.load("BTCUSD")
    full = full.tz_convert("UTC") if full.index.tz is not None else full.tz_localize("UTC")
    if tf == "15m":
        return full[["Open", "High", "Low", "Close", "Volume", "spread_close"]]
    rule = {"30m": "30min", "1h": "1h", "4h": "4h"}[tf]
    agg = full.resample(rule).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum",
                                   "spread_close": "last"})
    return agg.dropna()


def signals(df):
    c, h, l = df["Close"], df["High"], df["Low"]
    d = c.diff()
    rsi2 = 100 - 100 / (1 + _rma(d.clip(lower=0), 2) / _rma((-d).clip(lower=0), 2).replace(0, np.nan))
    s200 = c.rolling(200).mean()
    up_, dn_ = h.diff(), -l.diff()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_w = _rma(tr, 14)
    pdi = 100 * _rma(up_.where((up_ > dn_) & (up_ > 0), 0.0), 14) / atr_w
    ndi = 100 * _rma(dn_.where((dn_ > up_) & (dn_ > 0), 0.0), 14) / atr_w
    adx = _rma(100 * (pdi - ndi).abs() / (pdi + ndi), 14)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    side = np.where((rsi2 < 10) & (c > s200), 1, np.where((rsi2 > 90) & (c < s200), -1, 0))
    side = np.where((adx >= 25).to_numpy(), side, 0)
    eff = np.maximum(df["spread_close"].to_numpy(), FLOOR)
    side = np.where(np.round(eff / (0.6 * atr), 9) < 0.2, side, 0)
    side[:250] = 0
    return side.astype(int), atr


def _walk(args):
    """Real-tick outcomes for one month's signals: (entry ns, side, net points, exit ns) - None where no live price."""
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
    for t0, side, close, R, spc in rows:
        last = np.searchsorted(T, t0) - 1
        if last < 0 or t0 - T[last] > STALE_NS:
            continue
        a, b = np.searchsorted(T, t0), np.searchsorted(T, t0 + HOLD_NS)
        if b <= a:
            continue
        f = side * (M[a:b] - close)
        tgt = 0.2 * R
        s_ = np.flatnonzero(f <= -R)
        g = np.flatnonzero(f >= tgt)
        qs, qg = (s_[0] if len(s_) else None), (g[0] if len(g) else None)
        if qg is not None and (qs is None or qg < qs):
            q, pts = qg, tgt
        elif qs is not None:
            q, pts = qs, f[qs]
        else:
            q, pts = len(f) - 1, f[-1]
        out.append((t0, side, pts - max(spc, FLOOR) / 2 - max(S[a + q], FLOOR) / 2, int(T[a + q])))
    return out


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_tick_study as cts
    import cfd_vote_search as vs
    import exness_data as ed
    long_sw, _, triple = cts.SWAP["BTCUSD"]
    print("BTC, the live rule unchanged on each candle size; $ per BTC; one at a time; real ticks; $10+ spread; swap.")
    print(f"  {'candles':8s} {'IN-SAMPLE   n   win%       net    PF  maxDD':>44s}   {'HELD-OUT   n   win%       net    PF  maxDD':>44s}   {'trades/day':>10s}")
    for tf in ("5m", "15m", "30m", "1h", "4h"):
        df = bars(tf)
        side, atr = signals(df)
        bar_ns = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}[tf] * 60 * 10 ** 9
        close_ns = df.index.asi8 + bar_ns
        idx = np.flatnonzero(side != 0)
        by_month = {}
        for i in idx:
            ts = pd.Timestamp(int(close_ns[i]), tz="UTC")
            by_month.setdefault((ts.year, ts.month), []).append(
                (int(close_ns[i]), int(side[i]), float(df["Close"].iloc[i]), float(3 * atr[i]), float(df["spread_close"].iloc[i])))
        jobs = [(y, m, by_month.get((y, m), [])) for (y, m) in ed.months_until(cts.LAST)]
        cands = []
        with ProcessPoolExecutor(max(1, min(6, (os.cpu_count() or 2) - 1))) as ex:
            for got in ex.map(_walk, jobs):
                cands.extend(got)
        cands.sort()
        rolls, cumw = vs.rollover_weights(cands[0][0], cands[-1][3] + 3 * 86400 * 10 ** 9, triple)
        taken, free = [], -1
        for t0, s, pts, t1 in cands:
            if t0 < free:
                continue
            nights = cumw[np.searchsorted(rolls, t1, side="right") - 1] - cumw[np.searchsorted(rolls, t0, side="right") - 1]
            taken.append((t0, pts + (nights * long_sw if s > 0 else 0.0)))
            free = t1 + COOLDOWN_NS
        cells = []
        for per in ("is", "oos"):
            x = np.array([v for t0, v in taken if (t0 < vs.SPLIT) == (per == "is")])
            st = vs.stats(x)
            cells.append(f"{st['n']:>5} {100 * st['win']:>5.1f}% {st['net']:>+9,.0f} {st['pf']:>5.2f} {st['dd']:>6,.0f}")
        days = (taken[-1][0] - taken[0][0]) / (86400 * 10 ** 9)
        print(f"  {tf:8s} {cells[0]:>44s}   {cells[1]:>44s}   {len(taken) / days:>10.1f}", flush=True)


if __name__ == "__main__":
    main()
