#!/usr/bin/env python3
"""
pasted_combos_study.py — the pasted "Fast Momentum Scalper" and "Structural Trend Rider" on 3 years
================================================================================
The user, 7 Oct 2026, pasted two indicator combinations (no other words; tested as the previous pastes were):

COMBINATION 1 - "The Fast Momentum Scalper (Best for Bank Nifty)": long when the close is above VWAP AND EMA 9 crosses
above EMA 21 AND RSI(14) > 60; short mirrored (below VWAP, crosses below, RSI < 40). Stop 1.5 x ATR(14), target 3 x ATR.
COMBINATION 2 - "The Structural Trend Rider (Best for Nifty 50)": long when the close is above EMA 50 AND above VWAP AND
ADX(14) > 20 (or 25) and rising AND +DI > -DI; short mirrored. Entered on the candle where all of it FIRST holds (a
fresh signal, also after an exit). Stop at the lowest low (highest high) of the last 5 candles; target 2 (or 2.5) x the
risk.

The paste names no timeframe or session: each is run on 5-minute (the previous paste's) and 15-minute (the tool's own)
candles, entries from the open, everything out by 15:15. One position per index. Measured exactly as
vwap_ema_atr_study.py (whose pricer is self-checked equal to the studies' ps.price): the at-the-money option on its real
expiry, India VIX scaled by the index's own volatility, 0.25% slippage a side and every charge, per lot; stop before
target inside a candle, a gap filled at the open. In-sample to 15 Aug 2025, then the held-out final year.

    python3 pasted_combos_study.py
"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import entry_timing_study as ets
import indicators as ind
import stop_day_study as sds
import vwap_ema_atr_study as vea

INDICES = sds.INDICES
OUT_BY = dt.time(15, 15)


def signals(df, combo, adx_min=20.0):
    """(long, short) boolean arrays, and the arrays the exits need."""
    c = df["Close"].to_numpy()
    vw = ind.vwap(df).to_numpy()
    if combo == 1:
        e9, e21 = ind.ema(df["Close"], 9).to_numpy(), ind.ema(df["Close"], 21).to_numpy()
        rsi = ind.rsi(df["Close"], 14).to_numpy()
        up = np.r_[False, (e9[1:] > e21[1:]) & (e9[:-1] <= e21[:-1])]
        dn = np.r_[False, (e9[1:] < e21[1:]) & (e9[:-1] >= e21[:-1])]
        return up & (c > vw) & (rsi > 60), dn & (c < vw) & (rsi < 40)
    e50 = ind.ema(df["Close"], 50).to_numpy()
    adx = ind.adx(df, 14).to_numpy()
    pdi, mdi = (s.to_numpy() for s in ind.plus_minus_di(df, 14))
    rising = np.r_[False, adx[1:] > adx[:-1]]
    long_ = (c > e50) & (c > vw) & (adx > adx_min) & rising & (pdi > mdi)
    short = (c < e50) & (c < vw) & (adx > adx_min) & rising & (mdi > pdi)
    return long_, short


def run(k, df, sigma_by_day, days, combo, tf, adx_min=20.0, rr=2.0, swing=5, sig=None):
    o, h, l, c = (df[x].to_numpy() for x in ("Open", "High", "Low", "Close"))
    long_, short = sig if sig is not None else signals(df, combo, adx_min)    # sig: (long, short) given by a caller (mix_study.py)
    atr = ind.atr(df, 14).to_numpy()
    idx = df.index
    step = pd.Timedelta(minutes=tf)
    last = (dt.datetime.combine(dt.date(2000, 1, 1), OUT_BY) - dt.timedelta(minutes=tf)).time()   # the bar that ends 15:15
    tod = np.array([t.time() for t in idx])
    date = np.array([t.date() for t in idx])
    out, n, i = [], len(idx), 60
    while i < n - 1:
        d = date[i]
        fresh_l = long_[i] and not long_[i - 1] if combo == 2 else long_[i]
        fresh_s = short[i] and not short[i - 1] if combo == 2 else short[i]
        side = "CE" if fresh_l else "PE" if fresh_s else None
        if side is None or tod[i] >= last or date[i + 1] != d:
            i += 1
            continue
        ce = side == "CE"
        entry = c[i]
        if combo == 1:
            a = atr[i]
            if not a == a or a <= 0:
                i += 1
                continue
            stop, tgt = (entry - 1.5 * a, entry + 3.0 * a) if ce else (entry + 1.5 * a, entry - 3.0 * a)
        else:
            lo5, hi5 = l[max(0, i - swing + 1):i + 1].min(), h[max(0, i - swing + 1):i + 1].max()
            risk = entry - lo5 if ce else hi5 - entry
            if risk <= 0:
                i += 1
                continue
            stop, tgt = (lo5, entry + rr * risk) if ce else (hi5, entry - rr * risk)
        j, px, via = i + 1, None, "other"
        while j < n and date[j] == d:
            if (l[j] <= stop) if ce else (h[j] >= stop):
                px, via = (min(o[j], stop) if ce else max(o[j], stop)), "stop"
                break
            if (h[j] >= tgt) if ce else (l[j] <= tgt):
                px, via = (max(o[j], tgt) if ce else min(o[j], tgt)), "target"
                break
            if tod[j] >= last:
                px, via = c[j], "square-off"
                break
            j += 1
        if px is None:
            j -= 1
            px = c[j]
        t_in, t_out = idx[i] + step, idx[j] + step
        sig = sigma_by_day.get(d)
        net = vea.price_trade(k, days, t_in, entry, t_out, px, sig, ce) if sig and sig == sig else None
        if net is not None:
            out.append({"when": t_in, "exit_time": t_out, "net": net, "side": side, "closed_via": via, "index": k})
        i = j + 1
    return out


SPECS = [  # (label, combo, timeframe, adx_min, rr)
    ("C1 scalper, 5-min", 1, 5, None, None), ("C1 scalper, 15-min", 1, 15, None, None),
    ("C2 trend rider, 5-min, ADX 20, 1:2", 2, 5, 20.0, 2.0), ("C2 trend rider, 5-min, ADX 25, 1:2", 2, 5, 25.0, 2.0),
    ("C2 trend rider, 5-min, ADX 20, 1:2.5", 2, 5, 20.0, 2.5),
    ("C2 trend rider, 15-min, ADX 20, 1:2", 2, 15, 20.0, 2.0), ("C2 trend rider, 15-min, ADX 25, 1:2", 2, 15, 25.0, 2.0),
    ("C2 trend rider, 15-min, ADX 20, 1:2.5", 2, 15, 20.0, 2.5),
]


def _job(k):
    c = sds._context()
    df15 = c["hists"][k]
    days = set(df15.index.date)
    sig = c["F"][k]["sigma"]
    sigma_by_day = sig.groupby(sig.index.date).first().to_dict()
    df5 = ets.load_5m(k)
    res = {}
    for label, combo, tf, adx_min, rr in SPECS:
        res[label] = run(k, df5 if tf == 5 else df15[["Open", "High", "Low", "Close", "Volume"]], sigma_by_day, days, combo, tf,
                         adx_min or 20.0, rr or 2.0)
    return k, res


def main():
    from concurrent.futures import ProcessPoolExecutor
    print("Running both pasted combinations on 3 years, all three indices...", flush=True)
    with ProcessPoolExecutor(max_workers=len(INDICES)) as ex:
        got = dict(ex.map(_job, INDICES))
    print("=" * 172)
    print(" NIFTY + Bank Nifty + Sensex, per lot, after costs, real expiries.  columns: trades, won, total, profit factor, "
          "worst drop, worst single DAY")
    print("=" * 172)
    print(f" {'':46s} {'IN-SAMPLE (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print("-" * 172)
    print(" THE TOOL'S OWN SYSTEM (as live, 15-minute) -    2005   39% ₹  +437,509 PF 1.25 DD ₹  91,774 day ₹ -16,860  |"
          " 1167   35% ₹  +125,227 PF 1.12 DD ₹ 102,077 day ₹ -20,572")
    for label, *_ in SPECS:
        print(sds.line(label, [r for k in INDICES for r in got[k][label]]))
    print("-" * 172)
    print(" ON THE INDEX EACH IS SAID TO BE BEST FOR (the tool's own system on that index alone: Bank Nifty +172,293 / -5,376;"
          " Nifty +154,936 / +30,386)")
    for label, combo, *_ in SPECS:
        k = "BANKNIFTY" if combo == 1 else "NIFTY"
        print(sds.line(f"{label} - {k} only", got[k][label]))


if __name__ == "__main__":
    main()
