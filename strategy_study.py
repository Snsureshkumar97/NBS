#!/usr/bin/env python3
"""
strategy_study.py — the classic entry conditions, measured the same way
================================================================================
WHAT THIS IS, AND WHAT IT IS NOT

    It is NOT a bake-off of standalone strategies. backtest_intraday.run()
    replays history through this tool's own signal engine; a `gate` only
    answers "may this signal be taken?". Direction, strike, targets and stop
    stay the engine's. So what is measured here is whether a well-known entry
    condition IMPROVES this engine - not whether MACD beats Bollinger in the
    abstract. Presenting it as the latter would be dressing a filter test up
    as something it is not.

THE MULTIPLE-COMPARISONS PROBLEM, STATED BEFORE THE RESULTS

    Twenty candidates are tested here. On any dataset, the best of twenty will
    look good by luck alone - that is arithmetic, not pessimism. Two defences,
    both declared before the run:

      * Textbook parameters only. EMA 20/50, RSI 14 at 70/30, Bollinger 20/2,
        Donchian 20, MACD 12/26/9, Supertrend 10/3. Nothing swept. A swept
        parameter is a third source of luck on top of the twenty.
      * A candidate is only believed if it beats the live rules IN-SAMPLE AND
        on the held-out year. The count tested is reported with the result so
        it can be discounted honestly.

    Anything that wins one period and loses the other is a description of the
    past. That is the same bar the VIX-ratio idea failed on 13 Sep 2026.

    python3 strategy_study.py
"""
import math

import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import indicators as ind
import pro_study as ps
import regime_study as rs

INDICES = ps.INDICES
SPLIT = rs.SPLIT


def indicators_for(df):
    """Every classic entry condition, per bar, using only finished bars.

    Each series is shifted one bar: a condition is read at the close of bar i
    and the entry happens at that close, so the value must not contain the bar
    it is deciding.
    """
    close, high, low = df["Close"], df["High"], df["Low"]
    out = {}

    ema_f = close.ewm(span=20, adjust=False).mean()
    ema_s = close.ewm(span=50, adjust=False).mean()
    out["ema_up"] = (ema_f > ema_s).shift(1)

    macd = (close.ewm(span=12, adjust=False).mean()
            - close.ewm(span=26, adjust=False).mean())
    signal = macd.ewm(span=9, adjust=False).mean()
    out["macd_up"] = (macd > signal).shift(1)

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    out["rsi"] = rsi.shift(1)

    mid = close.rolling(20).mean()
    sd = close.rolling(20).std()
    out["bb_up"] = (close > mid + 2 * sd).shift(1)
    out["bb_dn"] = (close < mid - 2 * sd).shift(1)

    out["don_up"] = (close >= high.rolling(20).max()).shift(1)
    out["don_dn"] = (close <= low.rolling(20).min()).shift(1)

    atr = ind.atr(df, 10)
    hl2 = (high + low) / 2
    upper, lower = hl2 + 3 * atr, hl2 - 3 * atr
    trend = pd.Series(index=df.index, dtype=float)
    dirn, prev_u, prev_l = 1, np.nan, np.nan
    for i in range(len(df)):
        u, l, c = upper.iloc[i], lower.iloc[i], close.iloc[i]
        if not np.isfinite(u) or not np.isfinite(l):
            trend.iloc[i] = np.nan
            continue
        if np.isfinite(prev_u):
            u = min(u, prev_u) if c <= prev_u else u
            l = max(l, prev_l) if c >= prev_l else l
        if c > u:
            dirn = 1
        elif c < l:
            dirn = -1
        trend.iloc[i] = dirn
        prev_u, prev_l = u, l
    out["st_up"] = (trend > 0).shift(1)

    tp = (high + low + close) / 3
    day = pd.Series(df.index.date, index=df.index)
    cum_tp = (tp * df["Volume"]).groupby(day).cumsum()
    cum_v = df["Volume"].groupby(day).cumsum().replace(0, np.nan)
    vwap = (cum_tp / cum_v).ffill()
    out["above_vwap"] = (close > vwap).shift(1)

    dc = rs.daily_close(df)
    dh = high.groupby(day).max()
    dl = low.groupby(day).min()
    out["pdh"] = day.map(dh.shift(1))
    out["pdl"] = day.map(dl.shift(1))
    out["close"] = close
    return out


def build_gates(df, base):
    """name -> gate(i, rec), each layered on the live opening-range break."""
    X = indicators_for(df)
    c = X["close"].to_numpy()
    ema, macd = X["ema_up"].to_numpy(), X["macd_up"].to_numpy()
    rsi = X["rsi"].to_numpy()
    bbu, bbd = X["bb_up"].to_numpy(), X["bb_dn"].to_numpy()
    dnu, dnd = X["don_up"].to_numpy(), X["don_dn"].to_numpy()
    st, vw = X["st_up"].to_numpy(), X["above_vwap"].to_numpy()
    pdh, pdl = X["pdh"].to_numpy(), X["pdl"].to_numpy()

    def t(v):                       # a NaN-safe truth test
        return bool(v) if v == v and v is not None else False

    def call(rec):
        return rec["option_type"] == "CE"

    G = {}
    G["EMA 20/50 agrees"]      = lambda i, r: base(i, r) and (t(ema[i]) == call(r))
    G["MACD agrees"]           = lambda i, r: base(i, r) and (t(macd[i]) == call(r))
    G["Supertrend agrees"]     = lambda i, r: base(i, r) and (t(st[i]) == call(r))
    G["price on VWAP side"]    = lambda i, r: base(i, r) and (t(vw[i]) == call(r))
    G["Donchian 20 break"]     = lambda i, r: base(i, r) and (t(dnu[i]) if call(r) else t(dnd[i]))
    G["Bollinger break"]       = lambda i, r: base(i, r) and (t(bbu[i]) if call(r) else t(bbd[i]))
    G["Bollinger reversion"]   = lambda i, r: base(i, r) and (t(bbd[i]) if call(r) else t(bbu[i]))
    G["RSI not overbought/sold"] = lambda i, r: base(i, r) and (
        rsi[i] == rsi[i] and (rsi[i] < 70 if call(r) else rsi[i] > 30))
    G["RSI reversion"]         = lambda i, r: base(i, r) and (
        rsi[i] == rsi[i] and (rsi[i] < 30 if call(r) else rsi[i] > 70))
    G["breaks prior-day level"] = lambda i, r: base(i, r) and (
        (pdh[i] == pdh[i] and c[i] > pdh[i]) if call(r)
        else (pdl[i] == pdl[i] and c[i] < pdl[i]))
    G["inside prior-day range"] = lambda i, r: base(i, r) and (
        pdh[i] == pdh[i] and pdl[i] == pdl[i] and pdl[i] < c[i] < pdh[i])
    G["EMA + MACD agree"]      = lambda i, r: base(i, r) and (t(ema[i]) == call(r)) \
                                              and (t(macd[i]) == call(r))
    return G


def main():
    print("Loading history (cached)...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    A = {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(),
                "cl": df["Close"].to_numpy(),
                "end": (pd.Series(df.index.date)
                        != pd.Series(df.index.date).shift(-1)).to_numpy(),
                "days": set(df.index.date)}
    base = {k: rs.make_gates(F[k])["opening-range break"] for k in INDICES}
    gates = {k: build_gates(hists[k], base[k]) for k in INDICES}
    # The gates this codebase already knows, scored beside the new ones.
    for k in INDICES:
        existing = rs.make_gates(F[k])
        for nm in ("no stalled trend", "with daily trend", "skip 11:30-13:30",
                   "OR break + skip lunch", "all four"):
            g = existing[nm]
            gates[k][nm + " (existing)"] = g
    names = list(gates[INDICES[0]].keys())

    def score(gate_for):
        rows = []
        for k in INDICES:
            df = hists[k]
            res = bt.run(k, df, gate=gate_for(k))
            pos = {t: n for n, t in enumerate(df.index)}
            sig = F[k]["sigma"].to_numpy()
            for tr in res["trades"]:
                i = pos[tr["when"]]
                sg = sig[i]
                if not (sg == sg) or sg <= 0:
                    continue
                legs = ps.simulate(A[k], i, tr)
                r = ps.price(k, df, A[k], i, tr, legs, sg)
                if r:
                    rows.append(r)
        return ({"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
                 "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}, rows)

    live, _ = score(lambda k: base[k])
    def line(name, r):
        i, o = r["is"], r["oos"]
        if not i or not o:
            return f" {name:<28} (too few trades)"
        return (f" {name:<28} {i['n']:>5} ₹{i['total']:>+10,.0f} PF {i['pf']:.2f}"
                f"  | {o['n']:>5} ₹{o['total']:>+10,.0f} PF {o['pf']:.2f}")

    print("\n" + "=" * 104)
    print(f" {len(names)} classic entry conditions, each layered on the live rules")
    print(" per lot, pooled across the three indices, real expiries, after Zerodha costs")
    print("=" * 104)
    print(f" {'condition':<28} {'IN-SAMPLE: n, net, PF':>32}  | {'HELD-OUT: n, net, PF':>30}")
    print("-" * 104)
    print(line("LIVE RULES (OR break)", live))
    print("-" * 104)

    results = {}
    for name in names:
        r, _ = score(lambda k, n=name: gates[k][n])
        results[name] = r
        print(line(name, r))

    print("\n" + "=" * 104)
    print(" VERDICT — kept only if it beats the live rules in BOTH periods")
    print("=" * 104)
    li, lo = live["is"], live["oos"]
    survivors = []
    for name, r in results.items():
        i, o = r["is"], r["oos"]
        if not i or not o:
            continue
        di, do = i["total"] - li["total"], o["total"] - lo["total"]
        verdict = "KEEP" if (di > 0 and do > 0) else "drop"
        if verdict == "KEEP":
            survivors.append(name)
        print(f"   {verdict:<5} {name:<28} in-sample {di:>+11,.0f}   held-out {do:>+11,.0f}")

    print("\n" + "-" * 104)
    print(f" {len(names)} conditions were tested. With that many candidates the best")
    print(" one is expected to look good by luck, so a single winner proves little;")
    print(" what matters is whether it survives BOTH periods, and how large the")
    print(" margin is against the live rules.")
    if survivors:
        print(f" Survived both: {', '.join(survivors)}")
    else:
        print(" Nothing beat the live rules on both periods. The rules stand.")

    print("\n" + "=" * 104)
    print(" HOW MUCH ANY OF THIS SHOULD BE TRUSTED")
    print("=" * 104)
    print(" regime_study prices the SAME trades under different assumptions and")
    print(" the pooled held-out result moves like this:")
    print("     priced as expiry-day options   +497,265")
    print("     default assumption              +20,709")
    print("     priced six days out            -155,040")
    print("     no slippage                    +196,918")
    print("     double slippage                  -6,733")
    print(" A ranking that survives one set of assumptions and not the others is")
    print(" a property of the assumptions. Read the table above with that in mind:")
    print(" the differences between candidates are smaller than this spread.")


if __name__ == "__main__":
    main()
