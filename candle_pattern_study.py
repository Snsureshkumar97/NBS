#!/usr/bin/env python3
"""
candle_pattern_study.py — does a candlestick-pattern filter improve today's live entries?
================================================================================
The user, 29 Sep 2026: "do we use candle strategy and does it improve the tool or help the tool
anyway if you can find a github repo check it and test it."

THE TOOL DOES NOT USE CLASSIC CANDLESTICK PATTERNS TODAY
    "Candle"/"candlestick" in this codebase means the OHLC chart itself (chart_panel.py's canvas,
    the Lightweight-Charts CandlestickSeries in web_server.py) - price DATA, not a pattern-
    recognition strategy. signal_engine.py's entries come from ADX/EMA/RSI/VWAP trend reads, the
    option chain and reachability - never a shape like Hammer or Engulfing.

WHAT WAS FOUND ON GITHUB, AND WHY THIS DOES NOT WRAP ANY OF IT DIRECTLY
    Searched for a Python candlestick-pattern repo worth pulling in. l33tquant/candlestick is Rust,
    not Python. thehermit9/candle-pattern-scanner is Python but has 0 stars, 1 fork, and does not
    document its own pattern thresholds. Nothing found was both Python and well-established enough
    to trust blind. The pattern DEFINITIONS themselves are not actually in dispute, though - almost
    every one of those repos wraps or reimplements TA-Lib's CDL* functions, the de facto reference.
    So this uses the same textbook shape rules TA-Lib and every serious source describe, implemented
    directly - consistent with indicators.py's own hand-rolled ADX/EMA: this codebase does not
    depend on talib or pandas_ta, and neither is installed here.

    Patterns tested: Bullish Engulfing, Bearish Engulfing, Hammer, Shooting Star, and Doji (used as
    a veto - an indecisive bar - not a direction, matching standard usage).

METHODOLOGY - REUSES reversal_exit_study.py'S OWN BAR, NOT A NEW APPROXIMATION
    Same three years of real NIFTY/BANKNIFTY/SENSEX 15-minute history, the same real entry gates
    (tickets.py's _regime_hold / _divergence_hold / _reward_hold / _spread_hold, imported directly
    from reversal_exit_study.live_gate rather than re-approximated), the same live exit (hold to
    T2/stop, ps.simulate's default), the same real-expiry option pricing and after-costs stats().
    The ONLY variable across the two rows below: whether the entry bar's own candle also shows a
    pattern confirming that trade's direction. Kept only if requiring one improves total profit
    both in-sample AND on the held-out final year - this project's own standing bar.

WHAT THIS DOES NOT DO
    This does not test candlestick patterns as a STANDALONE strategy (entering on a pattern alone,
    with no trend/regime read) - the tool's entries are never trend-blind today, and testing a
    trend-blind alternative would not describe anything the tool could actually adopt. This is a
    FILTER study: would requiring a pattern improve the entries the tool already takes.

    python3 candle_pattern_study.py
"""
import numpy as np
import pandas as pd

import backtest_intraday as bt
import pro_study as ps
import regime_study as rs
import reversal_exit_study as res

INDICES = ps.INDICES
SPLIT = ps.SPLIT


# ---------------------------------------------------------------- pattern detection
def candle_patterns(df):
    """(bullish, bearish, doji) boolean arrays, one entry per bar, from that bar's own OHLC and the
    bar before it. TA-Lib's own CDL* thresholds - see the module docstring for why these and not a
    library."""
    o = df["Open"].to_numpy(dtype=float)
    h = df["High"].to_numpy(dtype=float)
    l = df["Low"].to_numpy(dtype=float)
    c = df["Close"].to_numpy(dtype=float)
    body = np.abs(c - o)
    rng = np.maximum(h - l, 1e-9)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l

    doji = body <= 0.1 * rng

    hammer = (lower >= 2 * body) & (upper <= body) & (body > 0)
    shooting_star = (upper >= 2 * body) & (lower <= body) & (body > 0)

    bull_body = c > o
    bear_body = c < o
    prev_o = np.roll(o, 1)
    prev_c = np.roll(c, 1)
    prev_bear = prev_c < prev_o
    prev_bull = prev_c > prev_o
    bullish_engulf = bull_body & prev_bear & (o <= prev_c) & (c >= prev_o)
    bearish_engulf = bear_body & prev_bull & (o >= prev_c) & (c <= prev_o)
    bullish_engulf[0] = False
    bearish_engulf[0] = False   # no bar before the first

    bullish = hammer | bullish_engulf
    bearish = shooting_star | bearish_engulf
    return bullish, bearish, doji


# ---------------------------------------------------------------- the gate
def pattern_gate(index_key, i, rec, df, bullish, bearish, doji):
    """Today's real entry gates, PLUS: the entry bar's own candle has to show a pattern confirming
    this trade's direction, and not be a Doji (indecision - a veto regardless of direction)."""
    if not res.live_gate(index_key, i, rec, df):
        return False
    if doji[i]:
        return False
    return bool(bullish[i]) if rec["option_type"] == "CE" else bool(bearish[i])


def main():
    print("Loading history (shared cache with pro_study.py / backtest_intraday.py)...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    import math
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    A = {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
                "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
                "days": set(df.index.date)}

    print("Computing candlestick patterns at every bar...")
    pats = {k: candle_patterns(hists[k]) for k in INDICES}
    for k in INDICES:
        bullish, bearish, doji = pats[k]
        n = len(bullish)
        print(f"  {k:10s} {n:>6} bars   bullish {bullish.sum():>5} ({100*bullish.sum()/n:4.1f}%)"
              f"   bearish {bearish.sum():>5} ({100*bearish.sum()/n:4.1f}%)"
              f"   doji {doji.sum():>5} ({100*doji.sum()/n:4.1f}%)")

    base_gate = {k: (lambda i, rec, k=k: res.live_gate(k, i, rec, hists[k])) for k in INDICES}
    filt_gate = {k: (lambda i, rec, k=k: pattern_gate(k, i, rec, hists[k], *pats[k])) for k in INDICES}

    def run_one(gate_map):
        rows = []
        n_trades_raw = 0
        for k in INDICES:
            df = hists[k]
            result = bt.run(k, df, gate=gate_map[k])
            n_trades_raw += len(result["trades"])
            pos = {t: n for n, t in enumerate(df.index)}
            sig = F[k]["sigma"].to_numpy()
            for tr in result["trades"]:
                i = pos[tr["when"]]
                s = sig[i]
                if not (s == s) or s <= 0:
                    continue
                legs = ps.simulate(A[k], i, tr)
                p = ps.price(k, df, A[k], i, tr, legs, s)
                if p:
                    rows.append(p)
        return {"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
                "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}, rows, n_trades_raw

    def line(name, r):
        a, b = r["is"], r["oos"]
        return (f" {name:34s} {a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    hdr = (f" {'variant':34s} {'IN-SAMPLE: trades, total, profit factor, worst drawdown':50s}"
           f"  | OUT-OF-SAMPLE (held-out final year)")
    print("=" * 132)
    print(" All per lot, pooled across NIFTY / BANKNIFTY / SENSEX, real expiries, after costs")
    print(" Entries: today's real gates (unchanged). Exit: hold to T2/stop (live, today), unchanged.")
    print(" Only variable: whether a candlestick pattern confirming direction is ALSO required.")
    print("=" * 132)
    print(hdr)
    print("-" * 132)

    base, base_rows, base_raw = run_one(base_gate)
    print(line("today's live gate (baseline)", base))

    filt, filt_rows, filt_raw = run_one(filt_gate)
    print(line("+ requires a confirming candle pattern", filt))
    kept_pct = 100 * filt_raw / base_raw if base_raw else 0.0
    print(f"   (kept {filt_raw} of {base_raw} entries that clear today's real gates - {kept_pct:.1f}%)")
    print("-" * 132)

    print("\n VERDICT — kept only if better than the baseline both in-sample and out-of-sample")
    di = filt["is"]["total"] - base["is"]["total"]
    do = filt["oos"]["total"] - base["oos"]["total"]
    dd = filt["oos"]["dd"] - base["oos"]["dd"]
    keep = di > 0 and do > 0
    print(f"   {'KEEP ' if keep else 'DROP '} candle-pattern filter   in-sample {di:>+10,.0f}   "
          f"out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")
    verdict_line = ("This clears the project's own bar to be adopted as a real entry filter."
                    if keep else
                    "This does NOT clear the bar; no candle-pattern filter is added anywhere.")
    print(f"\n   {verdict_line}")
    return {"base": base, "filtered": filt}


if __name__ == "__main__":
    main()
