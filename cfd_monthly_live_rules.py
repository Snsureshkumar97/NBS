#!/usr/bin/env python3
"""
cfd_monthly_live_rules.py — month by month, 3 years, the Exness rules that are LIVE now, at 0.25 lot
================================================================================
The user, 4 Oct 2026: "whats the p&L for 0.25 lot for last 3 years every month for crypto both the
markets". The live rules (config.CFD_RULES):
  BTC   RSI-2 with ADX >= 25, stop 3 x ATR, one target at 0.2 x the stop, no trade when the spread is
        20%+ of the target (the 87% forward test)
  GOLD  day trend + hour trend + 3-hour momentum, ADX rising and volume rising, stop 3 x ATR, one
        target at 0.75 x the stop
Every trade off the real-tick outcome tables (cfd_outcomes.py), one position at a time, 20 minutes
after an exit, Exness's overnight swap on buys - and the SPREAD CHARGED AT LEAST WHAT THE DEMO ACCOUNT
PAYS TODAY ($10 on BTC, $0.26 on gold - the archive's own is lower since 2026: $7 / $0.18; before,
$15-17 / $0.11): each trade's entry and exit spread is max(the archive's, today's). An approximation
for the exit (the entry bar's spread stands for it), on the side of caution.
$ at 0.25 lot (0.25 BTC; 25 oz of gold). In-sample = to 15 Aug 2025, the years the rules were chosen
on; held-out = after, which they were not.

    python3 cfd_monthly_live_rules.py
"""
import os
import sys

import numpy as np
import pandas as pd

LOTS = 0.25
FLOOR = {"BTCUSD": 10.0, "XAUUSD": 0.26}


def trades(sym, key, mult):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_rules
    import cfd_tick_study as cts
    import cfd_vote_search as vs
    import config
    import exness_data as ed
    plan = config.CFD_RULES[key]
    full = ed.load(sym)
    a = cfd_rules.compute(full[["Open", "High", "Low", "Close", "Volume"]])
    vv = np.stack([a[v] for v in plan["votes"]]).astype(np.int64)
    side = np.where(np.all(vv == vv[0], axis=0) & (vv[0] != 0), vv[0], 0)
    for f in plan["filters"]:
        side = np.where(a[f], side, 0)
    table = "outcomes_close" if plan["target_r"] < 0.5 else "outcomes"
    O = np.load(os.path.join(ed._dir(), f"{table}_{sym}.npz"))
    k = list(O["stops"]).index(plan["stop_atr"])
    j = int(np.argmin(np.abs(O["targets"] - plan["target_r"])))
    assert abs(O["targets"][j] - plan["target_r"]) < 1e-9, (O["targets"], plan["target_r"])
    spc = full["spread_close"].to_numpy()
    eff = np.maximum(spc, FLOOR[sym])
    if plan.get("max_spread_share"):
        side = np.where(np.round(eff / (plan["target_r"] * plan["stop_atr"] * a["atr"]), 9) < plan["max_spread_share"],
                        side, 0)
    net = O["net"][:, :, k:k + 1, j:j + 1].astype(float) - (eff - spc)[:, None, None, None]
    ext = O["ext"][:, :, k:k + 1, j:j + 1]
    entry = full.index.tz_convert("UTC").asi8 + 15 * 60 * 10 ** 9
    long_sw, _, triple = cts.SWAP[sym]
    rolls, cumw = vs.rollover_weights(entry[0], entry[-1], triple)
    sim = vs.Sim(net, ext, O["ok"], entry, long_sw, rolls, cumw, mult)
    idx = np.flatnonzero((side != 0) & O["ok"])
    when, usd = sim.run(idx, side[idx].astype(np.int64), 0, 0)
    return pd.DataFrame({"when": pd.to_datetime(when, utc=True).tz_convert("Asia/Kolkata"), "usd": usd * LOTS}), plan, vs.SPLIT


def main():
    out = {}
    for sym, key, mult in (("BTCUSD", "BTC", 1.0), ("XAUUSD", "GOLD", 100.0)):
        df, plan, split = trades(sym, key, mult)
        df["month"] = df["when"].dt.strftime("%Y-%m")
        g = df.groupby("month")["usd"]
        out[key] = pd.DataFrame({"n": g.size(), "win": g.apply(lambda x: 100 * (x > 0).mean()), "usd": g.sum()})
        out[key + "_all"] = df
    months = sorted(set(out["BTC"].index) | set(out["GOLD"].index))
    split_m = pd.Timestamp(split, tz="UTC").tz_convert("Asia/Kolkata").strftime("%Y-%m")
    print(f"$ at {LOTS} lot (0.25 BTC; 25 oz gold), real ticks, spread at least today's ($10 / $0.26), swap. "
          f"Months from {split_m} on: held-out (the split is mid-month).\n")
    print(f"{'month':8s} {'BTC trades  win%       P&L':>27s}   {'GOLD trades  win%       P&L':>28s}   {'BOTH':>9s} {'running':>9s}")
    run_ = 0.0
    year = {}
    for m in months:
        b = out["BTC"].loc[m] if m in out["BTC"].index else None
        gd = out["GOLD"].loc[m] if m in out["GOLD"].index else None
        bu, gu = (b["usd"] if b is not None else 0.0), (gd["usd"] if gd is not None else 0.0)
        run_ += bu + gu
        y = m[:4]
        year.setdefault(y, [0.0, 0.0])
        year[y][0] += bu; year[y][1] += gu
        cell = lambda r: (f"{int(r['n']):>6} {r['win']:>5.0f}% {r['usd']:>+10,.0f}" if r is not None else f"{'-':>6} {'':>5}  {'':>10}")
        mark = "  <- held-out from here" if m == split_m else ""
        print(f"{m:8s} {cell(b):>27s}   {cell(gd):>28s}   {bu + gu:>+9,.0f} {run_:>+9,.0f}{mark}")
    print("\nby year (calendar):")
    for y, (bu, gu) in year.items():
        print(f"  {y}: BTC {bu:>+9,.0f}   GOLD {gu:>+9,.0f}   both {bu + gu:>+9,.0f}")
    for key in ("BTC", "GOLD"):
        d = out[key + "_all"]
        eq = d["usd"].cumsum().to_numpy()
        dd = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq))
        mo = out[key]["usd"]
        print(f"  {key}: {len(d)} trades, {100 * (d['usd'] > 0).mean():.1f}% won, total {d['usd'].sum():+,.0f}; "
              f"worst drawdown {dd:,.0f}; {int((mo > 0).sum())} of {len(mo)} months up, worst month {mo.min():+,.0f} "
              f"({mo.idxmin()}), best {mo.max():+,.0f} ({mo.idxmax()})")


if __name__ == "__main__":
    main()
