#!/usr/bin/env python3
"""
target_ladder_study.py — 3 years of real Zerodha candles, T1 vs T2 vs T3, same entries
========================================================================================
The user, 28 Sep 2026: "can you give me back test of 3 years from zerodha separately
what are the results for t1 and t2 and t3" - so this holds the entry fixed and only
varies which rung of the ladder the trade is told to exit at, the same way
reversal_exit_study.py holds entries fixed and only varies the EXIT rule.

ENTRIES: today's real gates - tickets.py's _regime_hold / _divergence_hold /
_reward_hold / _spread_hold, called directly (see reversal_exit_study.py's docstring
for why this is used instead of pro_study.py's own "LIVE RULES" label, which predates
several changes and no longer describes what the tool actually enters on today).
Identical entries feed all three exit targets below - only the exit differs, so any
difference between the three rows is the target alone, not a different set of trades.

PRICING: pro_study.py's own - the real contract (real weekly/monthly expiry on the
exchange calendar in force that day), Black-Scholes off VIX-scaled realised
volatility, Zerodha's real costs (brokerage, STT, exchange/SEBI/stamp, GST, 0.25%
slippage a side). Stop is always checked before the target inside a bar - the
pessimistic order, since the bar alone cannot say which came first.

DATA: 3 years of real Kite 15-minute candles, pooled across NIFTY / BANKNIFTY /
SENSEX, from the shared cache (~/trading-tool-logs/history) other study files in
this project also use - delete that folder to force a fresh download.

WHAT THIS CANNOT TELL YOU: same limits as pro_study.py throughout - IV held constant
across a trade (no IV crush), today's lot sizes applied to the whole 3 years, and the
daily loss brake is NOT applied here (each target is measured on every gated entry,
not on what a day would actually have let through) so it is not directly the live
tool's day-to-day P&L - it isolates the target choice alone.

    python3 target_ladder_study.py
"""
import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import pro_study as ps
import regime_study as rs
import reversal_exit_study as res

INDICES = ps.INDICES
SPLIT = ps.SPLIT


def line(name, a, label_w=16):
    return (f" {name:{label_w}s} {a['n']:>5} ₹{a['total']:>+11,.0f}  "
            f"avg ₹{a['avg']:>+7,.0f}  win {a['win']:4.1f}%  PF {a['pf']:4.2f}  DD ₹{a['dd']:>9,.0f}")


def main():
    print("Loading 3 years of history (shared cache with pro_study.py)...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    for k, df in hists.items():
        print(f"  {k}: {df.index[0].date()} to {df.index[-1].date()}, {len(df):,} candles")
    vix = rs.load_vix()
    import math
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    A = {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(),
                 "cl": df["Close"].to_numpy(),
                 "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
                 "days": set(df.index.date)}
    gate = {k: (lambda i, rec, k=k: res.live_gate(k, i, rec, hists[k])) for k in INDICES}

    print("Replaying today's real entry gates over 3 years...")
    trades_by_index = {}
    for k in INDICES:
        r = bt.run(k, hists[k], gate=gate[k])
        trades_by_index[k] = r["trades"]
        print(f"  {k}: {len(r['trades']):,} entries")

    def run_target(target):
        """Same entries as every other target - only `target` (t1/t2/t3) changes
        which rung pro_study.simulate() treats as the exit, alongside the same
        stop every rung shares."""
        rows = []
        for k in INDICES:
            df = hists[k]
            pos = {t: n for n, t in enumerate(df.index)}
            sig = F[k]["sigma"].to_numpy()
            for tr in trades_by_index[k]:
                i = pos[tr["when"]]
                s = sig[i]
                if not (s == s) or s <= 0:
                    continue
                legs = ps.simulate(A[k], i, tr, target=target)
                p = ps.price(k, df, A[k], i, tr, legs, s)
                if p:
                    rows.append(p)
        return rows

    print("Pricing each rung as the real option, after real costs...")
    by_target = {t: run_target(t) for t in ("t1", "t2", "t3")}

    live = str(getattr(config, "EXIT_AT_TARGET", "T2") or "T2").lower()
    print("=" * 100)
    print(f" Entries: today's real gates, unchanged across all three rows below - only the EXIT rung differs.")
    print(f" Pricing: real expiries, real Zerodha costs, 0.25% slippage a side. Live tool exits at "
          f"{live.upper()} today (config.EXIT_AT_TARGET).")
    print("=" * 100)

    print("\nFULL 3 YEARS, POOLED ACROSS NIFTY / BANKNIFTY / SENSEX (per lot, after costs)")
    print("-" * 100)
    for t in ("t1", "t2", "t3"):
        all_stats = ps.stats(by_target[t])
        mark = "  <- live exit today" if t == live else ""
        print(line(f"{t.upper()}{mark}", all_stats, label_w=24))

    print("\nSPLIT BY PERIOD (in-sample to " + str(SPLIT.date()) + " | held-out final year after)")
    print("-" * 100)
    hdr = f" {'target':24s} {'IN-SAMPLE':^42s}  | {'HELD-OUT':^42s}"
    print(hdr)
    for t in ("t1", "t2", "t3"):
        rows = by_target[t]
        a = ps.stats([r for r in rows if r["when"] < SPLIT])
        b = ps.stats([r for r in rows if r["when"] >= SPLIT])
        mark = "  <- live" if t == live else ""
        print(f" {t.upper()+mark:24s} "
              f"{a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
              f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    print("\nBY INDEX, FULL 3 YEARS (per lot, after costs)")
    print("-" * 100)
    for k in INDICES:
        print(f" {k}")
        for t in ("t1", "t2", "t3"):
            idx_rows = [r for r in by_target[t] if r["index"] == k]
            st = ps.stats(idx_rows)
            if st is None:
                print(f"   {t.upper():6s} no priced trades")
                continue
            print("  " + line(t.upper(), st, label_w=6))

    print("\nNOTE: the daily loss brake (DAILY_LOSS_LIMIT_R) is NOT applied above - see this file's own")
    print("docstring. Real index points only up to the entry gate; everything past it is priced as an")
    print("option the way pro_study.py always has been - an optimistic model, not a record of real fills.")
    return by_target


if __name__ == "__main__":
    main()
