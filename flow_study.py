#!/usr/bin/env python3
"""
flow_study.py — a candle stand-in for taker flow, tested before trusted
================================================================================
Asked for by the user on 21 Sep 2026: "does the Indian market not need taker
flow to improve the tool". Real taker flow (who crossed the spread) has no
history anywhere - Delta serves only the latest prints, NSE publishes no
aggressor at all - so it cannot be backtested. What can be tested is the
nearest thing a candle holds: WHERE the bar closed inside its range, weighted
by the volume it traded. A bar that closes at its high on heavy volume looks
like buying pressure; one that closes at its low, like selling.

    bar delta  = volume * ((close - low) - (high - close)) / (high - low)
    flow       = sum of bar delta over the last 8 bars / sum of volume over them
                 (-1 all selling .. +1 all buying; 8 bars = 2 hours of 15-minute candles)

Same discipline as gann_volume_study.py: each idea is ONE variant declared
before looking, layered on the rules the tool runs now, kept only if it beats
the live rules in-sample AND on the held-out final year. Nothing here changes
the tool; it reports.

    D1 "flow agrees"           take a CE only when flow > 0, a PE only when flow < 0
    D2 "flow agrees strongly"  the same, but beyond +0.10 / -0.10
    D3 "not against"           skip an entry only when flow opposes it by 0.15 or more

WHAT THIS CAN AND CANNOT SAY
    It says whether a volume-weighted candle-pressure gate improves the rules on
    BITCOIN, whose candles carry real volume (the perpetual's). It does not say
    whether the real taker flow would - the candle only guesses at who was hitting
    the book - and it cannot be run on the Indian indices at all: their candles
    carry no volume, and the near-month future's intraday volume history is not
    obtainable (Zerodha's continuous history is daily only; expired contracts'
    intraday candles are not served). So for India the answer is "cannot be tested
    on history", not "no".

    python3 flow_study.py
"""
import numpy as np
import pandas as pd

import backtest_intraday as bt
import pro_study as ps

WINDOW = 8
STRONG, AGAINST = 0.10, 0.15


def flow_series(df, window=WINDOW):
    h, l, c, v = (df[k].to_numpy(dtype=float) for k in ("High", "Low", "Close", "Volume"))
    rng = h - l
    clv = np.divide((c - l) - (h - c), rng, out=np.zeros_like(rng), where=rng > 0)
    num = pd.Series(v * clv).rolling(window).sum()
    den = pd.Series(v).rolling(window).sum()
    return (num / den.replace(0, np.nan)).to_numpy()


def features(df):
    return {"flow": flow_series(df) if (df["Volume"] > 0).any() else None}


def _flow_at(X, i):
    f = X["flow"]
    v = f[i] if f is not None else np.nan
    return v if v == v else None


def d1_agrees(X):
    def gate(i, r):
        f = _flow_at(X, i)
        return f is not None and (f > 0 if r["option_type"] == "CE" else f < 0)
    return gate


def d2_strong(X):
    def gate(i, r):
        f = _flow_at(X, i)
        return f is not None and (f >= STRONG if r["option_type"] == "CE" else f <= -STRONG)
    return gate


def d3_not_against(X):
    def gate(i, r):
        f = _flow_at(X, i)
        if f is None:
            return True
        return not (f <= -AGAINST if r["option_type"] == "CE" else f >= AGAINST)
    return gate


VARIANTS = {"D1 flow agrees": d1_agrees, "D2 flow agrees strongly": d2_strong, "D3 not against": d3_not_against}


def study_bitcoin():
    df = bt.fetch_history("BTC", years=3, use_cache=True)
    X = features(df)
    split = ps.SPLIT.tz_convert(df.index.tz) if df.index.tz is not None else ps.SPLIT.tz_localize(None)

    def evaluate(extra):
        g = extra(X) if extra else None
        res = bt.run("BTC", df, gate=g)
        tr = res["trades"]

        def stats(sel):
            if not sel:
                return None
            p = [(float(t["exit"]) - float(t["entry"])) * (1.0 if t["side"] == "CE" else -1.0) for t in sel]
            wins = [x for x in p if x > 0]
            losses = [x for x in p if x < 0]
            pf = (sum(wins) / -sum(losses)) if losses else float("inf")
            eq = peak = dd = 0.0
            for x in p:
                eq += x
                peak = max(peak, eq)
                dd = max(dd, peak - eq)
            return {"n": len(p), "total": sum(p), "pf": pf, "dd": dd}
        return {"is": stats([t for t in tr if t["when"] < split]), "oos": stats([t for t in tr if t["when"] >= split])}

    def line(name, r):
        f = lambda s: (f"{s['n']:>5} {s['total']:>+10,.0f} pts PF {s['pf']:4.2f} DD {s['dd']:>8,.0f}" if s else "   no trades")
        return f" {name:28s} {f(r['is'])}  | {f(r['oos'])}"

    print(f"Bitcoin: {len(df)} fifteen-minute candles, {df.index[0]} to {df.index[-1]}; "
          f"{int((df['Volume'] > 0).sum())} carry volume; split at {split}")
    print("=" * 120)
    print(" BITCOIN - index points per contract, no option pricing.  in-sample | held-out (from 15 Aug 2025)")
    print("=" * 120)
    base = evaluate(None)
    print(line("LIVE RULES", base))
    out = {}
    for name, fn in VARIANTS.items():
        out[name] = evaluate(fn)
        print(line("live + " + name, out[name]))
    print("\n VERDICT - kept only if better than LIVE RULES both in-sample and held-out")
    for name, r in out.items():
        if not (r["is"] and r["oos"] and base["is"] and base["oos"]):
            print(f"   drop  {name:28s} too few trades to compare")
            continue
        di = r["is"]["total"] - base["is"]["total"]
        do = r["oos"]["total"] - base["oos"]["total"]
        dd = r["oos"]["dd"] - base["oos"]["dd"]
        kept = di > 0 and do > 0
        print(f"   {'KEEP ' if kept else 'drop '} {name:28s} in-sample {di:>+10,.0f}   held-out {do:>+10,.0f}"
              f"   held-out drawdown {dd:>+9,.0f}   trades kept {r['is']['n'] + r['oos']['n']} of {base['is']['n'] + base['oos']['n']}")
    return base, out


if __name__ == "__main__":
    study_bitcoin()
