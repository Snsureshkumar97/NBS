#!/usr/bin/env python3
"""
cfd_monthly_results.py — month by month, 3 years: Bitcoin and gold on Exness's REAL ticks
================================================================================
The user, 4 Oct 2026: "give me the results monthly wise for last 3 years for both crypto
markets". The live setup (config.CFD_EXIT_PLAN: one target at 5 x the stop distance, no T1
step, no trail; the live entries - BTC ADX 20, gold ADX 25; 24-hour limit; the reversal exit)
walked on Exness's own ticks (cfd_tick_study.walk), one position at a time, after the spread
actually quoted and the overnight swap (BUYS only on this account type, triple on BTC's Friday
and gold's Wednesday - today's rates applied to all three years). The engine's old target
ladder (T1 moves the stop, Supertrend trail, exit at T2) beside it for comparison.

$ per 1 lot (1 BTC; 100 oz of gold). At 0.25 lot multiply by 0.25.

    python3 cfd_monthly_results.py
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

VARIANTS = {"live 5R": (("r", 5.0, 5.0), False, False, 0),
            "old ladder": (None, True, True, 0)}


def run(sym):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_tick_study as t
    import config
    import crypto_strategy_study as cs
    import exness_data as ed
    rows = []
    with ProcessPoolExecutor(max(1, min(6, (os.cpu_count() or 2) - 1))) as ex:
        for got in ex.map(t._job, [(sym, y, m, VARIANTS) for (y, m) in ed.months_until(t.LAST)]):
            rows.extend(got)
    long_sw, short_sw, triple = t.SWAP[sym]
    for r in rows:
        r["net"] += t.rollovers(r["when"], r["exit_time"], triple) * (long_sw if r["side"] == "CE" else short_sw)
    out = {}
    for name in VARIANTS:
        vr = sorted((r for r in rows if r["variant"] == name), key=lambda r: r["when"])
        out[name] = cs.sequential(vr, cooldown_min=config.REENTRY_COOLDOWN_MIN, waiver_adx=config.ADX_TREND_THRESHOLD)
    return out


def monthly(rows):
    if not rows:
        return pd.DataFrame(columns=["trades", "wins", "net"])
    df = pd.DataFrame({"month": [r["when"].tz_convert("Asia/Kolkata").strftime("%Y-%m") for r in rows],
                       "net": [r["net"] for r in rows], "win": [r["net"] > 0 for r in rows]})
    g = df.groupby("month")
    return pd.DataFrame({"trades": g.size(), "wins": g["win"].sum(), "net": g["net"].sum()})


def main():
    res = {sym: run(sym) for sym in ("BTCUSD", "XAUUSD")}
    tabs = {(sym, v): monthly(res[sym][v]) for sym in res for v in VARIANTS}
    months = sorted(set().union(*[t_.index for t_ in tabs.values()]))
    print(f"{'month':8s} | {'BTC trades':>10s} {'win%':>5s} {'net 5R':>10s} {'old':>9s} | "
          f"{'GOLD trades':>11s} {'win%':>5s} {'net 5R':>10s} {'old':>9s}")
    cum = {"BTCUSD": 0.0, "XAUUSD": 0.0}
    for m in months:
        cells = []
        for sym in ("BTCUSD", "XAUUSD"):
            a, b = tabs[(sym, "live 5R")], tabs[(sym, "old ladder")]
            if m in a.index:
                n, w, net = int(a.loc[m, "trades"]), int(a.loc[m, "wins"]), float(a.loc[m, "net"])
                cum[sym] += net
                old = float(b.loc[m, "net"]) if m in b.index else 0.0
                cells.append(f"{n:>10d} {100 * w / n:>4.0f}% {net:>+10,.0f} {old:>+9,.0f}")
            else:
                cells.append(f"{'-':>10s} {'':>5s} {'':>10s} {'':>9s}")
        print(f"{m:8s} | {cells[0]} | {' ' + cells[1]}")
    print()
    for sym in ("BTCUSD", "XAUUSD"):
        a = tabs[(sym, "live 5R")]
        b = tabs[(sym, "old ladder")]
        print(f"{sym}: {int(a['trades'].sum())} trades, {100 * a['wins'].sum() / max(1, a['trades'].sum()):.0f}% won, "
              f"net {a['net'].sum():+,.0f} (old ladder {b['net'].sum():+,.0f}); "
              f"{(a['net'] > 0).sum()} of {len(a)} months positive; best {a['net'].max():+,.0f} "
              f"({a['net'].idxmax()}), worst {a['net'].min():+,.0f} ({a['net'].idxmin()})")
        for y in sorted({m[:4] for m in a.index}):
            sel = a[[m.startswith(y) for m in a.index]]
            print(f"   {y}: {int(sel['trades'].sum())} trades, net {sel['net'].sum():+,.0f}")


if __name__ == "__main__":
    main()
