#!/usr/bin/env python3
"""
mid_entry_study.py — what a mid-price entry would have to save, against what it may miss (3 years)
================================================================================
The user, 6 Oct 2026: "yes build the mid price entry and do a backtest on 3 years". A true replay is impossible: no
historical bid/ask exists for Indian options (the index history is candles; the per-minute option recording began 28
Sep 2026, without bid/ask), so whether a mid-price limit would have filled cannot be known for past trades. What 3
years CAN answer is the trade-off itself, on the live system as configured (opening_window_study's baseline: the live
entry checks, the live exit with the 2-hour breakeven priced as it fills, one position per index, per lot after costs):

  saving   every FILLED entry bought s% of its premium cheaper (half the spread, at the most, on a mid fill)
  missing  m% of entries never fill - assumed the WORST ones to miss: the trades whose price ran away in the first
           5 minutes after the signal (3-year 5-minute index candles), i.e. the moves a patient limit gets left behind
           on, which tend to be winners. A random m% is shown too.

    python3 mid_entry_study.py
"""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_intraday as bt
import conditional_cooldown_study as ccs
import config
import entry_timing_study as ets
import indicators as ind
import opening_window_study as ows
import pro_study as ps
import regime_study as rs
import reversal_exit_study as res

INDICES = rs.INDICES
SAVINGS = (0.0, 0.001, 0.0025, 0.005, 0.01, 0.02)
MISSES = (0.0, 0.05, 0.10, 0.20, 0.30)


def price_detail(key, df, A, i, tr, legs, sigma):
    """pro_study.price()'s money, plus the premium paid (rupees per lot, before slippage)."""
    meta = config.INSTRUMENTS[key]
    step, qty = meta["strike_step"], meta["lot_size"]
    when = df.index[i] + pd.Timedelta(minutes=15)
    exp = ps.expiry_on_or_after(key, when.date(), A["days"])
    K = round(tr["entry"] / step) * step
    p0 = rs.bs(tr["entry"], K, ps.years_to(exp, when), sigma, tr["side"] == "CE")
    p = ps.price(key, df, A, i, tr, legs, sigma)
    if p is None:
        return None
    p["premium_paid"] = p0 * qty
    return p


def main():
    print("Loading history...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    target = str(getattr(config, "EXIT_AT_TARGET", "T2") or "T2").lower()
    rows = []
    for k in INDICES:
        df = hists[k]
        F = rs.features(df, vix, nrv)
        A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
             "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
             "days": set(df.index.date)}
        pre = bt.precompute(df, k)
        st_len, st_mult = config.supertrend_params(k)
        st = ind.supertrend(df, st_len, st_mult)[0].to_numpy()
        df5 = ets.load_5m(k)
        out = bt.run(k, df, gate=lambda i, rec, k=k: res.live_gate(k, i, rec, hists[k]))
        pos = {t: n for n, t in enumerate(df.index)}
        sig = F["sigma"].to_numpy()
        cands = []
        for tr in out["trades"]:
            i = pos[tr["when"]]
            s = sig[i]
            if not (s == s) or s <= 0:
                continue
            legs = ows.simulate_live(A, i, tr, st, target)
            p = price_detail(k, df, A, i, tr, legs, s)
            if p is None:
                continue
            exit_px, exit_bar, _ = legs[-1]
            p["side"] = tr["side"]
            p["closed_via"] = ("stop" if abs(exit_px - tr["stop"]) < 1e-6 else
                               "target" if tr.get(target) is not None and abs(exit_px - tr[target]) < 1e-6 else "other")
            p["exit_adx"] = float(pre["adx"].iloc[exit_bar])
            # how far the index ran the trade's way in the first 5 minutes after the entry
            t0 = p["when"]
            j = df5.index.searchsorted(t0)
            run = 0.0
            if j < len(df5) and df5.index[j] - t0 < pd.Timedelta(minutes=5):
                run = (float(df5["High"].iloc[j]) - tr["entry"]) if tr["side"] == "CE" else (tr["entry"] - float(df5["Low"].iloc[j]))
            p["run5"] = run / max(float(pre["atr"].iloc[i]), 1e-9)          # in ATRs, comparable across indices
            cands.append(p)
        cands.sort(key=lambda r: r["when"])
        rows += ccs.sequential_trades_conditional(cands, base_cooldown_min=config.REENTRY_COOLDOWN_MIN,
                                                  waive_adx_threshold=config.ADX_TREND_THRESHOLD)
    rng = np.random.default_rng(20261006)

    def total(sel, s, period):
        x = [r for r in sel if (r["when"] < rs.SPLIT) == (period == "is")]
        return sum(r["net"] + s * r["premium_paid"] for r in x)

    for period in ("is", "oos"):
        base = [r for r in rows if (r["when"] < rs.SPLIT) == (period == "is")]
        print(f"\n{'IN-SAMPLE (to 15 Aug 2025)' if period == 'is' else 'HELD-OUT FINAL YEAR'}: {len(base)} trades, "
              f"today's method ₹{total(base, 0, period):+,.0f}; average premium paid ₹{np.mean([r['premium_paid'] for r in base]):,.0f} a lot")
        order = sorted(base, key=lambda r: -r["run5"])              # the fastest runners first = the first to be missed
        print(f"   change vs today (₹, 3 indices, per lot)  saving per filled entry ->  "
              + "  ".join(f"{100 * s:>5.2f}%" for s in SAVINGS))
        for m in MISSES:
            n_miss = int(round(m * len(base)))
            missed = set(id(r) for r in order[:n_miss])
            kept = [r for r in base if id(r) not in missed]
            cells = [total(kept, s, period) - total(base, 0, period) for s in SAVINGS]
            print(f"   miss {int(100 * m):>2d}% (the fastest runners)          " + "  ".join(f"{c:>+9,.0f}" for c in cells))
        for m in (0.10, 0.20):
            draws = []
            for _ in range(200):
                idx = rng.choice(len(base), size=int(round(m * len(base))), replace=False)
                miss = set(idx.tolist())
                kept = [r for n, r in enumerate(base) if n not in miss]
                draws.append(total(kept, 0.005, period) - total(base, 0, period))
            print(f"   (miss {int(100 * m)}% at RANDOM, saving 0.50%: {np.mean(draws):+,.0f} on average)")
        fast = order[:int(round(0.10 * len(base)))]
        print(f"   the 10% fastest runners made ₹{sum(r['net'] for r in fast):+,.0f} of today's ₹{total(base, 0, period):+,.0f}"
              f" ({100 * np.mean([r['net'] > 0 for r in fast]):.0f}% of them won, vs {100 * np.mean([r['net'] > 0 for r in base]):.0f}% overall)")


if __name__ == "__main__":
    main()
