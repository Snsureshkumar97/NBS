#!/usr/bin/env python3
"""
cfd_target_study.py — are the Exness BTC / gold targets too far? Closer ones, tested
================================================================================
The user, 3 Oct 2026, after the switch to Exness: "its giving targets way to far ...
the targets are way to far from the entry".

WHY THEY ARE FAR. Targets are spot + reach x REACH_FRACTIONS (0.4 / 0.7 / 1.0), where
reach is the tightest of three limits: the option market's expected move, the OI
walls, and the day's remaining typical range. Exness has no options, so only the
day's range is left - and on Bitcoin, the last 90 days of live-engine entries had a
median T1 of $665, T2 (the live exit) $1,163 and T3 $1,662 (2.4%) against a $317
stop: T3 5.1x the risk, reached 13% of the time inside the 24-hour hold.

THE TEST. The SAME entries (the live engine's, cached by exness_adx_study.py: BTC at
ADX 20, gold at its live 25), the live exit (crypto_strategy_study.run_live: stop,
T1 moves the stop to T1, the Supertrend trail after T1, the exit at T2 - the live
EXIT_AT_TARGET - the reversal exit, 96 bars, no 2h breakeven), one position at a
time, Exness's own spread charged in and out. Only where T1 and T2 sit changes:

    today         reach x 0.4 / 0.7 (what is live now)
    R a/b         T1 = a x risk, T2 = b x risk (risk = entry to stop)
    cap a/b       today's levels, but never further than a x / b x risk

KEEP a variant only if it beats today on net in BOTH the in-sample and held-out
periods. Swap is not included (as before).

    python3 cfd_target_study.py
"""
import os
import pickle
import sys

import numpy as np

VARIANTS = [("today", None)] + [(f"R {a:g}/{b:g}", ("r", a, b)) for a, b in
                                ((0.5, 1.0), (0.75, 1.5), (1.0, 1.5), (1.0, 2.0), (1.5, 2.5), (1.5, 3.0))] \
           + [(f"cap {a:g}/{b:g}", ("cap", a, b)) for a, b in ((1.0, 2.0), (1.5, 3.0))]
SYMBOLS = {"BTCUSD": ("BTC", 1.0, 20, "per 1 BTC"), "XAUUSD": ("GOLD", 100.0, 25, "per lot (100 oz)")}


def levels(tr, how):
    """(t1, t2) for one entry under a variant; the stop and entry never move."""
    if how is None:
        return tr["t1"], tr["t2"]
    kind, a, b = how
    sign = 1 if tr["side"] == "CE" else -1
    risk = abs(tr["entry"] - tr["stop"])
    r1, r2 = tr["entry"] + sign * a * risk, tr["entry"] + sign * b * risk
    if kind == "r":
        return r1, r2
    # cap: whichever is nearer the entry
    near = (lambda x, y: min(x, y)) if sign > 0 else (lambda x, y: max(x, y))
    return near(tr["t1"], r1), near(tr["t2"], r2)


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import backtest_intraday as bt
    import config
    import crypto_strategy_study as cs
    import exness_adx_study as ex
    import exness_data as ed
    import indicators as ind

    for sym, (key, mult, adx_gate, unit) in SYMBOLS.items():
        full = ed.load(sym)
        df = full[["Open", "High", "Low", "Close", "Volume"]]
        sp_close, sp_avg = full["spread_close"].to_numpy(), full["spread_avg"].to_numpy()
        path = os.path.join(ed._dir(), f"adx_{sym}_{adx_gate}_{len(df)}.pkl")
        if not os.path.exists(path):
            ex._job((sym, adx_gate))
        with open(path, "rb") as fh:
            d = pickle.load(fh)
        adx_arr = bt.precompute(df, key)["adx"].to_numpy()
        st_line = ind.supertrend(df, *config.supertrend_params(key))[0].to_numpy()

        print("\n" + "=" * 132)
        print(f" EXNESS {sym} - {len(d['entries']):,} live-engine entries (ADX {adx_gate}), live exit at T2, "
              f"net after Exness's spread, $ {unit}")
        print("=" * 132)
        print(f" {'targets':12s} {'IN-SAMPLE: n, net, PF, win%, maxDD':>42s}   {'HELD-OUT':>38s}   "
              f"{'LIVE WINDOW':>24s}   {'median T2 away':>14s}")
        results = {}
        for name, how in VARIANTS:
            entries = []
            for tr in d["entries"]:
                t1, t2 = levels(tr, how)
                entries.append(dict(tr, t1=t1, t2=t2))
            rows = cs.run_live(df, None, entries, d["opp"], st_line, adx_arr, be_bars=0, exit_key="t2")
            for r in rows:
                i, j = r["i"], r["j"]
                out_sp = sp_close[j] if r["closed_via"] == "other" else sp_avg[j]
                r["net"] = ex.price_cfd(r["side"], r["entry"], r["exit"], sp_close[i], out_sp) * mult
            results[name] = rows
            p = cs.periods(rows)
            away = np.median([abs(e["t2"] - e["entry"]) for e in entries if e["t2"] is not None])

            def c(rs, wide=True):
                s = cs.stats(rs, "net")
                return (f"{s['n']:>5} {s['total']:>+11,.0f} PF{s['pf']:>5.2f} {s['win']:>4.0f}% DD{s['dd']:>9,.0f}"
                        if wide else f"{s['n']:>4} {s['total']:>+10,.0f} PF{s['pf']:>5.2f}")
            print(f" {name:12s} {c(p['is']):>42s}   {c(p['oos']):>38s}   {c(p['live'], False):>24s}   {away:>14,.2f}")

        base = cs.periods(results["today"])
        b_is, b_oos = cs.stats(base["is"], "net")["total"], cs.stats(base["oos"], "net")["total"]
        print("\n VERDICT vs today (KEEP only if better net in BOTH periods):")
        for name, _ in VARIANTS[1:]:
            p = cs.periods(results[name])
            a_is, a_oos = cs.stats(p["is"], "net")["total"], cs.stats(p["oos"], "net")["total"]
            v = "KEEP" if a_is > b_is and a_oos > b_oos else "drop"
            print(f"   {name:12s} in-sample {a_is - b_is:>+11,.0f}   held-out {a_oos - b_oos:>+11,.0f}   -> {v}")


if __name__ == "__main__":
    main()
