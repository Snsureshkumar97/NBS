#!/usr/bin/env python3
"""
external_strategy_comparison.py — a real GitHub strategy, our own pricing, 3yr
================================================================================
The user, 2 Oct 2026: "check on github if anyone have a good strategy and
run that strategy on 3 years and compare with my strategy who have the
higher profits."

THE STRATEGY: github.com/anirudhatalmale6-alt/nifty-banknifty-intraday-trend-algo
Picked over several other NIFTY/Bank Nifty repos found because it is the
most directly comparable to "my strategy" - same markets, same intraday
style, its own Indian cost model, a real test suite (82 tests including
lookahead checks), and an HONEST published result on its own 60-day sample:
"friction took essentially the entire edge" - net profit factor 1.01 over
just 32 trades. Worth testing properly on 3 years with real option pricing
rather than trusting that thin sample either way.

ENTRY - ALL SIX, verified against the repo's own strategy.py / indicators.py
/ config/nifty.yaml / config/banknifty.yaml directly, not approximated:
  1. HTF trend: on REAL resampled coarser candles (htf_multiplier=4 -> 1-hour
     bars from the native 15-minute ones), EMA(20) > EMA(50) AND the EMA(50)
     itself is RISING over the last 3 coarse bars.
  2. Supertrend - this project's OWN indicators.supertrend(), already an
     exact match to the repo's own stated tie-breaking convention ("the
     upper band only ratchets down while price stays below it", "direction
     flips only on a close through the active band") - confirmed by reading
     both implementations side by side, not assumed from the name alone.
     (10, 2.5) for NIFTY, (10, 3.0) for Bank Nifty - already the trade's
     own direction.
  3. Donchian breakout: close beyond the prior 20 bars' own high/low, shifted
     one bar (the repo's own stated convention, to avoid lookahead) plus
     breakout_buffer_atr * ATR - confirmed 0.0 in both configs' own
     dataclass default, so effectively no buffer either market.
  4. ADX(14) >= 20 (NIFTY) / 22 (Bank Nifty) - Wilder's RMA. This project's
     own indicators.adx() called with NO dx_length override (14/14 - its
     own default), matching the repo's plain Wilder ADX exactly - NOT this
     project's own live ADX_DX_SMOOTHING variant, which is a different,
     faster-reacting setting used only by this project's own signal.
  5. ATR(14)/close >= 0.0005 (NIFTY) / 0.0006 (Bank Nifty) - a volatility
     floor.
  6. EMA(9) > EMA(21) on the NATIVE bars - a separate, shorter pair from
     both the HTF check above and this project's own EMA_FAST/SLOW (20/50).
  Session gates, both markets: entries only 09:30-14:45, at most 3 a day,
  one position open at a time - the repo's own stated defaults. Fills at
  the NEXT bar's open after the signal bar's close ("next_bar_execution:
  true" - the repo's own words, not this project's usual same-bar-close
  convention).

EXIT, in the repo's own stated priority, checked from the fill bar onward:
  1. Initial stop at entry +/- stop_atr_multiplier x ATR (ATR frozen at the
     signal bar - 1.5 NIFTY / 1.75 Bank Nifty).
  2. Once price has moved breakeven_atr_multiplier (1.0) x that SAME frozen
     ATR favourably, the stop moves to breakeven and never loosens again.
  3. From breakeven on, the stop trails the live Supertrend line itself,
     ratcheting only favourably, same discipline as every other exit this
     project has ever tested.
  4. Closes if the native EMA(9)/EMA(21) crosses back against the trade.
  5. Fixed target: OFF by default (target_atr_multiplier=0.0) - matched,
     not simulated.
  6. Hard square-off at 15:15 - the repo's OWN stated time, not this
     project's own close convention, kept for fidelity to what is being
     tested.

WHAT IS NOT PORTED, AND WHY THIS IS STILL A FAIR COMPARISON
    The repo backtests the INDEX VALUE directly - its own README says so
    plainly: "index values are not directly tradeable; real fills happen in
    futures or options," an acknowledged simplification on ITS side, not a
    gap this port introduces. Every entry/exit SIGNAL (timing, direction,
    the exact move captured) is reproduced exactly as published; only HOW
    that signal gets priced changes - through THIS project's own real
    option pricing and real Zerodha cost model (pro_study.price(), the
    EXACT same pricing "my strategy" itself is measured through in every
    other study this project has run). That is the fairer comparison, not
    a weaker one - "my strategy" was never measured on raw index points
    either.

    No SENSEX here: the repo never published a config for it (only
    config/nifty.yaml and config/banknifty.yaml exist) - inventing one
    would be testing a strategy that was never actually offered for that
    market. "My strategy" below is measured on the SAME NIFTY+Bank Nifty
    pair, in the SAME run, for a guaranteed apples-to-apples total - not a
    number pulled from an earlier study's differently-scoped pool.

    python3 external_strategy_comparison.py
"""
import datetime as dt
import math

import numpy as np
import pandas as pd

import backtest_intraday as bt
import indicators as ind
import pro_study as ps
import regime_study as rs

INDICES = ("NIFTY", "BANKNIFTY")
SPLIT = rs.SPLIT

PARAMS = {
    "NIFTY": {"htf_mult": 4, "htf_ema_fast": 20, "htf_ema_slow": 50, "htf_slope_lb": 3,
              "ema_fast": 9, "ema_slow": 21, "st_len": 10, "st_mult": 2.5,
              "breakout_len": 20, "breakout_buffer_atr": 0.0,
              "adx_len": 14, "adx_threshold": 20.0, "atr_len": 14, "min_atr_pct": 0.0005,
              "stop_atr_mult": 1.5, "breakeven_atr_mult": 1.0},
    "BANKNIFTY": {"htf_mult": 4, "htf_ema_fast": 20, "htf_ema_slow": 50, "htf_slope_lb": 3,
                  "ema_fast": 9, "ema_slow": 21, "st_len": 10, "st_mult": 3.0,
                  "breakout_len": 20, "breakout_buffer_atr": 0.0,
                  "adx_len": 14, "adx_threshold": 22.0, "atr_len": 14, "min_atr_pct": 0.0006,
                  "stop_atr_mult": 1.75, "breakeven_atr_mult": 1.0},
}
ENTRY_START = dt.time(9, 30)
ENTRY_CUTOFF = dt.time(14, 45)
SQUARE_OFF = dt.time(15, 15)
MAX_TRADES_PER_DAY = 3


def htf_trend(df, p):
    """UP/DOWN/FLAT per native bar, from REAL resampled coarser candles,
    shifted one full coarse-bar slot before mapping back - a native bar
    inside a still-forming coarse bar only ever sees the LAST FULLY
    COMPLETED one, the same discipline this project already applied in
    htf_trend_study.py. Rule here is stricter than that file's: the repo
    also requires the slow EMA's own SLOPE over htf_slope_lb bars, not
    just fast > slow."""
    rule = f"{p['htf_mult'] * 15}min"
    htf = df["Close"].resample(rule).last().dropna()
    ema_f = ind.ema(htf, p["htf_ema_fast"])
    ema_s = ind.ema(htf, p["htf_ema_slow"])
    slope_up = ema_s > ema_s.shift(p["htf_slope_lb"])
    slope_down = ema_s < ema_s.shift(p["htf_slope_lb"])
    up = (ema_f > ema_s) & slope_up
    down = (ema_f < ema_s) & slope_down
    d = pd.Series(0, index=htf.index)
    d[up] = 1
    d[down] = -1
    return d.shift(1).reindex(df.index, method="ffill")


def build_features(df, p):
    adx = ind.adx(df, p["adx_len"])          # no dx_length override - plain Wilder, matching the repo
    atr = ind.atr(df, p["atr_len"])
    ema_f = ind.ema(df["Close"], p["ema_fast"])
    ema_s = ind.ema(df["Close"], p["ema_slow"])
    st_line, st_dir = ind.supertrend(df, p["st_len"], p["st_mult"])
    buf = p["breakout_buffer_atr"] * atr
    dc_upper = df["High"].rolling(p["breakout_len"]).max().shift(1)
    dc_lower = df["Low"].rolling(p["breakout_len"]).min().shift(1)
    htf = htf_trend(df, p)

    long_setup = ((htf == 1) & (st_dir == 1) & (df["Close"] > dc_upper + buf)
                  & (adx >= p["adx_threshold"]) & (atr / df["Close"] >= p["min_atr_pct"])
                  & (ema_f > ema_s))
    short_setup = ((htf == -1) & (st_dir == -1) & (df["Close"] < dc_lower - buf)
                   & (adx >= p["adx_threshold"]) & (atr / df["Close"] >= p["min_atr_pct"])
                   & (ema_f < ema_s))
    return {"long_setup": long_setup.to_numpy(), "short_setup": short_setup.to_numpy(),
            "atr": atr.to_numpy(), "ema_f": ema_f.to_numpy(), "ema_s": ema_s.to_numpy(),
            "st_line": st_line.to_numpy(), "hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(),
            "cl": df["Close"].to_numpy(), "op": df["Open"].to_numpy()}


def simulate_exit(feats, times, side, fill_bar, entry_price, atr_at_signal, p, n):
    """One leg: (exit_price, exit_bar, 1.0) - this strategy never partial-
    exits. Checked in the repo's own stated priority, from the fill bar
    itself onward (the stop can be hit the same bar it filled): stop first
    (pessimistic - this project's own universal convention, applied here
    too since the repo itself does not say which comes first within a
    bar), then the breakeven trigger, then the Supertrend trail update,
    then the EMA-cross exit, then the 15:15 hard square-off."""
    ce = side == "CE"
    stop = (entry_price - p["stop_atr_mult"] * atr_at_signal) if ce else \
           (entry_price + p["stop_atr_mult"] * atr_at_signal)
    be_trigger = (entry_price + p["breakeven_atr_mult"] * atr_at_signal) if ce else \
                 (entry_price - p["breakeven_atr_mult"] * atr_at_signal)
    be_done = False
    hi, lo, cl = feats["hi"], feats["lo"], feats["cl"]
    for j in range(fill_bar, n):
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            return [(stop, j, 1.0)]
        if not be_done and ((hi[j] >= be_trigger) if ce else (lo[j] <= be_trigger)):
            be_done = True
            stop = entry_price
        if be_done:
            st = feats["st_line"][j]
            stop = max(stop, st) if ce else min(stop, st)
        if (feats["ema_f"][j] < feats["ema_s"][j]) if ce else (feats["ema_f"][j] > feats["ema_s"][j]):
            return [(cl[j], j, 1.0)]
        if times[j] >= SQUARE_OFF:
            return [(cl[j], j, 1.0)]
    return [(cl[n - 1], n - 1, 1.0)]


def main():
    print("Loading history...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    base_gate = {k: rs.make_gates(F[k])["opening-range break"] for k in INDICES}

    rows_ext, rows_mine = [], []
    for k in INDICES:
        df = hists[k]
        p = PARAMS[k]
        feats = build_features(df, p)
        sig = F[k]["sigma"].to_numpy()
        n = len(df)
        times = df.index.time
        dates = df.index.date

        A = {"hi": feats["hi"], "lo": feats["lo"], "cl": feats["cl"],
             "end": (pd.Series(dates) != pd.Series(dates).shift(-1)).to_numpy(),
             "days": set(dates)}

        # --- "my strategy" on the SAME history, same run, for a guaranteed
        # apples-to-apples pool ---
        res = bt.run(k, df, gate=base_gate[k])
        pos = {t: m for m, t in enumerate(df.index)}
        for tr in res["trades"]:
            i = pos[tr["when"]]
            s = sig[i]
            if not (s == s) or s <= 0:
                continue
            legs = ps.simulate(A, i, tr)
            pr = ps.price(k, df, A, i, tr, legs, s)
            if pr:
                rows_mine.append(pr)

        # --- the external strategy, sequential (one position, 3/day, session gates) ---
        open_until_bar = -1
        trades_today = {}
        for i in range(max(100, p["breakout_len"] + 5), n - 1):
            if i <= open_until_bar:
                continue
            t = times[i]
            if t < ENTRY_START or t >= ENTRY_CUTOFF:
                continue
            day = dates[i]
            if trades_today.get(day, 0) >= MAX_TRADES_PER_DAY:
                continue
            side = None
            if feats["long_setup"][i]:
                side = "CE"
            elif feats["short_setup"][i]:
                side = "PE"
            if side is None:
                continue
            fill_bar = i + 1
            if fill_bar >= n or times[fill_bar] >= SQUARE_OFF:
                continue
            entry_price = feats["op"][fill_bar]
            atr_at_signal = feats["atr"][i]
            if not (atr_at_signal == atr_at_signal) or atr_at_signal <= 0:
                continue
            legs = simulate_exit(feats, times, side, fill_bar, entry_price, atr_at_signal, p, n)
            exit_bar = legs[-1][1]
            trades_today[day] = trades_today.get(day, 0) + 1
            open_until_bar = exit_bar
            s = sig[fill_bar]
            if not (s == s) or s <= 0:
                continue
            tr = {"side": side, "entry": entry_price, "stop": None,
                  "t1": None, "t2": None, "t3": None}
            pr = ps.price(k, df, A, fill_bar, tr, legs, s)
            if pr:
                rows_ext.append(pr)

    def stats_of(rows):
        return {"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
                "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}

    def line(name, r):
        a, b = r["is"], r["oos"]
        return (f" {name:30s} {a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    mine, ext = stats_of(rows_mine), stats_of(rows_ext)
    print("=" * 128)
    print(" NIFTY + Bank Nifty only (the external strategy has no SENSEX config), real expiries, after costs")
    print("=" * 128)
    hdr = (f" {'strategy':30s} {'IN-SAMPLE: trades, total, profit factor, worst drawdown':50s}"
           f"  | OUT-OF-SAMPLE (held-out final year)")
    print(hdr); print("-" * 128)
    print(line("MY strategy (LIVE RULES, OR break)", mine))
    print(line("GitHub: nifty-banknifty-intraday-trend-algo", ext))

    print("\n HIGHER PROFIT:")
    print(f"   in-sample:     {'MINE' if mine['is']['total'] >= ext['is']['total'] else 'the GitHub strategy'} "
          f"by ₹{abs(mine['is']['total'] - ext['is']['total']):,.0f}")
    print(f"   held-out:      {'MINE' if mine['oos']['total'] >= ext['oos']['total'] else 'the GitHub strategy'} "
          f"by ₹{abs(mine['oos']['total'] - ext['oos']['total']):,.0f}")
    return {"mine": mine, "external": ext}


if __name__ == "__main__":
    main()
