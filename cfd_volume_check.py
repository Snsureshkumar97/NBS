#!/usr/bin/env python3
"""
cfd_volume_check.py — the 87% BTC rule with "volume rising" added: better or not?
================================================================================
The user, 4 Oct 2026: "can you also add volume rising and check the backtest results". vol_rising is the
filter the search already had (cfd_rules.compute: the last 5 candles' tick volume above the last 20's - a
broker's quote feed has no traded volume, so ticks are the volume). ONE candidate, named before the run:
  base   the live rule exactly as cfd_monthly_live_rules.py has it (RSI-2 + ADX >= 25, stop 3 ATR, target
         0.2R, the spread check, the demo's $10 spread or more, swap) - real-tick outcomes, one at a time
  + vol  the same, and only when volume is rising
(and, for context only, the same when volume is NOT rising). KEEP only if better than the base in BOTH
periods AND the held-out beats 95% of 2,000 coin flips on its own bars.

    python3 cfd_volume_check.py
"""
import os
import sys

import numpy as np

FLOOR = 10.0
LOTS = 0.25


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_rules
    import cfd_tick_study as cts
    import cfd_vote_search as vs
    import config
    import exness_data as ed
    plan = config.CFD_RULES["BTC"]
    full = ed.load("BTCUSD")
    a = cfd_rules.compute(full[["Open", "High", "Low", "Close", "Volume"]])
    side = np.where((a["rsi2"] != 0) & a["adx25"], a["rsi2"], 0).astype(np.int64)
    O = np.load(os.path.join(ed._dir(), "outcomes_close_BTCUSD.npz"))
    k = list(O["stops"]).index(plan["stop_atr"])
    j = int(np.argmin(np.abs(O["targets"] - plan["target_r"])))
    spc = full["spread_close"].to_numpy()
    eff = np.maximum(spc, FLOOR)
    side = np.where(np.round(eff / (plan["target_r"] * plan["stop_atr"] * a["atr"]), 9) < plan["max_spread_share"], side, 0)
    net = O["net"][:, :, k:k + 1, j:j + 1].astype(float) - (eff - spc)[:, None, None, None]
    entry = full.index.tz_convert("UTC").asi8 + 15 * 60 * 10 ** 9
    long_sw, _, triple = cts.SWAP["BTCUSD"]
    rolls, cumw = vs.rollover_weights(entry[0], entry[-1], triple)
    sim = vs.Sim(net, O["ext"][:, :, k:k + 1, j:j + 1], O["ok"], entry, long_sw, rolls, cumw, 1.0)
    ins, oos = entry < vs.SPLIT, entry >= vs.SPLIT
    vol = a["vol_rising"].astype(bool)
    rng = np.random.default_rng(20261004)
    print(f"{'$ per BTC, one at a time':34s} {'IN-SAMPLE   n   win%      net    PF  maxDD':>44s}   "
          f"{'HELD-OUT   n   win%      net    PF  maxDD':>44s}   {'3 yrs at 0.25 lot':>17s}")
    res = {}
    for name, keep in (("base (live now)", np.ones(len(full), bool)), ("+ volume rising", vol),
                       ("volume NOT rising (context)", ~vol)):
        cells, tot = [], 0.0
        for per in (ins, oos):
            idx = np.flatnonzero(per & keep & (side != 0) & O["ok"])
            s = vs.stats(sim.run(idx, side[idx], 0, 0)[1])
            res[(name, per is ins)] = (s, idx)
            tot += s["net"]
            cells.append(f"{s['n']:>5} {100 * s['win']:>5.1f}% {s['net']:>+9,.0f} {s['pf']:>5.2f} {s['dd']:>6,.0f}")
        print(f"{name:34s} {cells[0]:>44s}   {cells[1]:>44s}   {LOTS * tot:>+16,.0f}", flush=True)
    (b_i, _), (b_o, _) = res[("base (live now)", True)], res[("base (live now)", False)]
    (v_i, _), (v_o, idx_o) = res[("+ volume rising", True)], res[("+ volume rising", False)]
    flips = np.array([sim.run(idx_o, rng.choice([-1, 1], len(idx_o)), 0, 0)[1].sum() for _ in range(2000)])
    p = (1 + np.sum(flips >= v_o["net"])) / 2001
    better = v_i["net"] > b_i["net"] and v_o["net"] > b_o["net"]
    print(f"\n+ volume rising vs the base: in-sample {v_i['net'] - b_i['net']:+,.0f}, held-out {v_o['net'] - b_o['net']:+,.0f} per BTC; "
          f"held-out coin flips as good {100 * p:.1f}% -> "
          + ("KEEP" if better and p <= 0.05 else "NOT better in both periods - DROP" if not better else "better in both, but fails the coin flip"))


if __name__ == "__main__":
    main()
