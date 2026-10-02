#!/usr/bin/env python3
"""
crypto_vote_study.py — which ENTRY VOTES should BTC use on Delta Exchange?
================================================================================
The user, 2 Oct 2026, after crypto_strategy_study.py found nothing profitable
after costs: "just try to improve btc with delta exchange with what votes we
have to enter the trades change that".

What changes here is ONLY the entry vote, through config.VOTE_OVERRIDES["crypto"]
- the per-market switch built for this (vote_overrides_test.py). Everything else
is the live system: backtest_intraday.run() with the real build_recommendation(),
tickets.py's own crypto gates (_reward_hold at 2.0, _spread_hold), the live exit
(crypto_strategy_study.walk_live / walk_live_premium - stop, T1 ratchet,
Supertrend trail, 2h breakeven, T2, reversal), one position at a time with the
live cooldown and its waiver. The reversal exit reads the SAME vote set as the
entry, as it would live, so each set gets its own per-bar signal pass.
Each set is also shown with the 2h breakeven removed - the one exit change
crypto_strategy_study.py found helped in every vehicle, both periods.

Priced as crypto_strategy_study.py prices everything: options premium-tracked as
live (next-day contract - the ~40-day one was far worse everywhere), the perp at
taker and at maker fees, and gross points (no costs) to see the raw edge.

THE VOTE SETS (pre-declared before any result was read; today = Trend, MACD,
RSI, VWAP; 3 must agree, at most 1 against; ADX >= 20):
    V1  unanimous: all 4 agree, none against
    V2  ADX >= 25            V3  ADX >= 30
    V4  + Supertrend vote    V5  + Volume vote    V6  + +DI/-DI vote
    V7  drop VWAP (a "session" VWAP is arbitrary in a 24/7 market)
    V8  drop RSI
    V9  + Supertrend + Volume, 4 must agree
    V10 + Supertrend, all 5 agree
    V11 + Supertrend + Volume + +DI/-DI, 4 must agree
PICKED ON IN-SAMPLE ONLY, then judged on held-out: a set is adopted only if it
beats today's in BOTH periods and is profitable after costs in both.

    python3 crypto_vote_study.py
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

KEY = "BTC"
SETS = {
    "V0  today (Trend,MACD,RSI,VWAP 3/1)": {},
    "V1  unanimous 4/0": {"min_agree": 4, "max_dissent": 0},
    "V2  ADX >= 25": {"adx": 25},
    "V3  ADX >= 30": {"adx": 30},
    "V4  + Supertrend": {"add": ["Supertrend"]},
    "V5  + Volume": {"add": ["Volume"]},
    "V6  + +DI/-DI": {"add": ["+DI/-DI"]},
    "V7  drop VWAP": {"drop": ["VWAP"]},
    "V8  drop RSI": {"drop": ["RSI"]},
    "V9  + Supertrend + Volume, 4 agree": {"add": ["Supertrend", "Volume"], "min_agree": 4},
    "V10 + Supertrend, all 5": {"add": ["Supertrend"], "min_agree": 5, "max_dissent": 0},
    "V11 + ST + Vol + DI, 4 agree": {"add": ["Supertrend", "Volume", "+DI/-DI"], "min_agree": 4},
}


def _entries_and_bias(name):
    """One vote set's live-engine entries and per-bar signal - in its own process,
    cached to disk so a re-run only prices."""
    import pickle
    import backtest_intraday as bt
    import config
    import crypto_data as cd
    import crypto_strategy_study as cs
    import reversal_exit_study as res
    import tickets

    df = cd.candles()
    tag = "".join(ch for ch in name.split()[0] if ch.isalnum())
    path = os.path.join(os.path.dirname(bt._cache_path(KEY, 3)), f"btc_votes_{tag}_{len(df)}.pkl")
    if os.path.exists(path):
        return name, path
    config.VOTE_OVERRIDES["crypto"] = dict(SETS[name])

    def gate(i, rec):
        return (tickets.TicketBook._reward_hold(cs._SELF, KEY, rec) is None
                and tickets.TicketBook._spread_hold(cs._SELF, rec) is None)

    entries = bt.run(KEY, df, gate=gate)["trades"]
    _, opp = res.bar_bias(KEY, df)
    with open(path, "wb") as fh:
        pickle.dump({"entries": entries, "opp": opp}, fh)
    return name, path


def main():
    import pickle
    import backtest_intraday as bt
    import config
    import crypto_strategy_study as cs
    import indicators as ind

    workers = max(1, min(len(SETS), (os.cpu_count() or 2) - 1))
    print(f"Replaying the live engine under {len(SETS)} vote sets on {workers} processes "
          "(cached after the first run)...", flush=True)
    paths = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for name, path in ex.map(_entries_and_bias, SETS):
            paths[name] = path
            print(f"  done: {name}", flush=True)

    df, iv, fund = cs.load()
    pre = bt.precompute(df, KEY)
    adx = pre["adx"].to_numpy()
    st_line = ind.supertrend(df, *config.supertrend_params(KEY))[0].to_numpy()

    R = {}
    for name in SETS:
        with open(paths[name], "rb") as fh:
            d = pickle.load(fh)
        for exit_name, kw in (("live exit", {}), ("no 2h breakeven", {"be_bars": 0})):
            idx = cs.price_all(cs.run_live(df, iv, d["entries"], d["opp"], st_line, adx, **kw), df, iv)
            prem = cs.run_live_premium(df, iv, d["entries"], d["opp"], st_line, adx, "next_day", **kw)
            R[(name, exit_name)] = {"gross": idx, "perp": idx, "perp_maker": idx, "opt_next_day": prem}

    def cell(rows, key):
        s = cs.stats(rows, key)
        return f"{s['n']:>4} {s['total']:>+9,.0f} PF{s['pf']:>5.2f}"

    print("\n" + "=" * 156)
    print(" BTC, $ per 1 BTC (x0.25 for 250 lots), after Delta fees + 18% GST (+2% option spread). n, total, PF.")
    print("=" * 156)
    for key, label in (("gross", "GROSS POINTS - no costs (the raw edge)"),
                       ("opt_next_day", "OPTIONS, next-day, premium-tracked as live (what the tool trades)"),
                       ("perp", "PERPETUAL, taker"),
                       ("perp_maker", "PERPETUAL, maker (only if resting limits fill)")):
        for exit_name in ("live exit", "no 2h breakeven"):
            print(f"\n {label}  —  {exit_name}")
            print(f" {'vote set':40s} {'IN-SAMPLE':>26s} {'HELD-OUT':>26s} {'LIVE WINDOW':>26s}")
            for name in SETS:
                p = cs.periods(R[(name, exit_name)][key])
                print(f" {name:40s} {cell(p['is'], key):>26s} {cell(p['oos'], key):>26s} {cell(p['live'], key):>26s}")

    print("\n VERDICT - picked on in-sample only; adopted only if it beats today's set in BOTH periods AND is")
    print(" profitable after costs in both")
    for key in ("opt_next_day", "perp", "perp_maker"):
        for exit_name in ("live exit", "no 2h breakeven"):
            base = cs.periods(R[("V0  today (Trend,MACD,RSI,VWAP 3/1)", "live exit")][key])
            b_is, b_oos = cs.stats(base["is"], key)["total"], cs.stats(base["oos"], key)["total"]
            best = max(SETS, key=lambda n: cs.stats(cs.periods(R[(n, exit_name)][key])["is"], key)["total"])
            p = cs.periods(R[(best, exit_name)][key])
            a, b = cs.stats(p["is"], key), cs.stats(p["oos"], key)
            ok = a["total"] > b_is and b["total"] > b_oos and a["total"] > 0 and b["total"] > 0
            print(f"   {key:12s} {exit_name:16s} best in-sample: {best:38s} IS {a['total']:>+9,.0f}  "
                  f"held-out {b['total']:>+9,.0f}  -> {'ADOPT' if ok else 'not good enough'}")
    return R


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
