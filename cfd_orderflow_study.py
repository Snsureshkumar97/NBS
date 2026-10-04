#!/usr/bin/env python3
"""
cfd_orderflow_study.py — does ORDER FLOW improve the 87% BTC rule?
================================================================================
The user, 4 Oct 2026: "can you check if order flow will improve the results". The flow is the TICK-RULE
proxy (cfd_tick_flow.py: up-ticks vs down-ticks of Exness's own quotes - true taker data is not in a
broker's feed, and Binance's refuses this location). Measured on the rule's own signal candle, signed
WITH the trade (+ = flow on the trade's side):
  flow now     the signal candle's imbalance (up - down) / (up + down)
  flow 1 h     the last 4 candles'
  flow 3 h     the last 12 candles'
  activity     the signal candle's ticks vs the 20 before it (a spike = a busy, maybe capitulating, candle)
THE BASE: the live rule exactly as cfd_monthly_live_rules.py has it (RSI-2 + ADX >= 25, stop 3 ATR,
target 0.2R, the spread check, the demo's $10 spread or more, swap) - real-tick outcomes, one at a time.
THE TEST, as cfd_rsi2_loss_study.py's: each measure split into thirds (cut points from the IN-SAMPLE
signals only); every "skip this third" and "only this third" (24 candidates) scored on the in-sample
signals (day-by-day t); the 3 best ONLY go to the held-out year: KEEP if better than the base in BOTH
periods and the held-out beats 1 - 0.05/3 of 2,000 coin flips.

    python3 cfd_orderflow_study.py
"""
import os
import sys

import numpy as np
import pandas as pd

FLOOR = 10.0
N_FLIPS = 2000


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_rules
    import cfd_tick_study as cts
    import cfd_vote_search as vs
    import config
    import exness_data as ed
    sym, key = "BTCUSD", "BTC"
    plan = config.CFD_RULES[key]
    full = ed.load(sym)
    a = cfd_rules.compute(full[["Open", "High", "Low", "Close", "Volume"]])
    side = np.where((a["rsi2"] != 0) & a["adx25"], a["rsi2"], 0).astype(np.int64)
    O = np.load(os.path.join(ed._dir(), f"outcomes_close_{sym}.npz"))
    k = list(O["stops"]).index(plan["stop_atr"])
    j = int(np.argmin(np.abs(O["targets"] - plan["target_r"])))
    spc = full["spread_close"].to_numpy()
    eff = np.maximum(spc, FLOOR)
    side = np.where(np.round(eff / (plan["target_r"] * plan["stop_atr"] * a["atr"]), 9) < plan["max_spread_share"], side, 0)
    net = O["net"][:, :, k:k + 1, j:j + 1].astype(float) - (eff - spc)[:, None, None, None]
    ext = O["ext"][:, :, k:k + 1, j:j + 1]
    ok = O["ok"]
    entry = full.index.tz_convert("UTC").asi8 + 15 * 60 * 10 ** 9
    long_sw, _, triple = cts.SWAP[sym]
    rolls, cumw = vs.rollover_weights(entry[0], entry[-1], triple)
    sim = vs.Sim(net, ext, ok, entry, long_sw, rolls, cumw, 1.0)
    ins, oos = entry < vs.SPLIT, entry >= vs.SPLIT

    F = np.load(os.path.join(ed._dir(), f"tickflow_{sym}.npz"))
    up, dn, ticks = (pd.Series(F[x]) for x in ("up", "down", "ticks"))
    imb = lambda n: ((up.rolling(n).sum() - dn.rolling(n).sum()) / (up.rolling(n).sum() + dn.rolling(n).sum()).replace(0, np.nan)).to_numpy()
    feats = {"flow now": side * imb(1), "flow 1 h": side * imb(4), "flow 3 h": side * imb(12),
             "activity": (ticks / ticks.shift().rolling(20).mean().replace(0, np.nan)).to_numpy()}

    sig = np.flatnonzero((side != 0) & ok)
    sd = (side[sig] < 0).astype(np.int64)
    t1 = ext[sig, sd, 0, 0]
    nights = cumw[np.searchsorted(rolls, t1, side="right") - 1] - cumw[np.searchsorted(rolls, entry[sig], side="right") - 1]
    v = net[sig, sd, 0, 0] + np.where(side[sig] > 0, nights * long_sw, 0.0)
    lose = v < 0
    day = entry // (86400 * 10 ** 9)

    def seq(mask):
        idx = np.flatnonzero(mask & ok & (side != 0))
        return idx, vs.stats(sim.run(idx, side[idx], 0, 0)[1])

    def day_t(keep_sig):
        m = keep_sig & ins[sig]
        if m.sum() < 300:
            return -np.inf
        _, inv = np.unique(day[sig][m], return_inverse=True)
        sums = np.bincount(inv, weights=v[m])
        return sums.mean() / (sums.std(ddof=1) / np.sqrt(len(sums)))

    _, b_is = seq(ins); _, b_oos = seq(oos)
    print(f"BASE - the live rule, $ per BTC: in-sample {b_is['n']} trades {100 * b_is['win']:.1f}% won {b_is['net']:+,.0f}; "
          f"held-out {b_oos['n']} trades {100 * b_oos['win']:.1f}% won {b_oos['net']:+,.0f}  "
          f"(every signal: {len(sig):,}, {100 * np.mean(~lose):.1f}% won)\n")
    print(" PART A - order flow at the signal, in thirds (cut points from in-sample signals): share, % LOST, $ per signal")
    print(f"  {'measure':10s} {'third':22s} {'IN-SAMPLE share lost%   $/sig':>30s}   {'HELD-OUT share lost%   $/sig':>30s}")
    cands = []
    for name, x in feats.items():
        xs = x[sig]
        q1, q2 = np.nanquantile(xs[ins[sig]], [1 / 3, 2 / 3])
        thirds = [("low", xs < q1, f"< {q1:+.2f}" if name != "activity" else f"< {q1:.2f}x"),
                  ("middle", (xs >= q1) & (xs < q2), f"{q1:+.2f} .. {q2:+.2f}" if name != "activity" else f"{q1:.2f}..{q2:.2f}x"),
                  ("high", xs >= q2, f">= {q2:+.2f}" if name != "activity" else f">= {q2:.2f}x")]
        for nm, m, lab in thirds:
            cells = []
            for per in (ins[sig], oos[sig]):
                mm = per & m
                cells.append(f"{100 * mm.sum() / max(per.sum(), 1):>5.0f}% {100 * np.mean(lose[mm]) if mm.any() else 0:>5.1f}% "
                             f"{v[mm].mean() if mm.any() else 0:>+7.1f}")
            print(f"  {name:10s} {nm + ' ' + lab:22s} {cells[0]:>30s}   {cells[1]:>30s}")
            full_m = np.zeros(len(full), bool); full_m[sig[m]] = True
            cands.append((f"skip {name} {nm} third", ~m, ~full_m))
            cands.append((f"only {name} {nm} third", m, full_m))
    base_t = day_t(np.ones(len(sig), bool))
    scored = sorted(((day_t(ms), nm, mf) for nm, ms, mf in cands), key=lambda z: -z[0])
    print(f"\n PART B - the 3 best of {len(cands)} filters by the in-sample day-by-day t (the base: {base_t:+.2f}), then held-out + coin flip:")
    rng = np.random.default_rng(20261004)
    kept = []
    for tt, nm, keep in scored[:3]:
        _, si = seq(ins & keep)
        idx_o, so = seq(oos & keep)
        flips = np.array([sim.run(idx_o, rng.choice([-1, 1], len(idx_o)), 0, 0)[1].sum() for _ in range(N_FLIPS)])
        pc = (1 + np.sum(flips >= so["net"])) / (1 + N_FLIPS)
        better = si["net"] > b_is["net"] and so["net"] > b_oos["net"]
        ok_ = better and pc <= 0.05 / 3
        kept += [nm] if ok_ else []
        print(f"  {nm:30s} t {tt:+.2f} | in-sample {si['n']:>5} {100 * si['win']:.1f}% {si['net']:>+9,.0f} | held-out {so['n']:>4} "
              f"{100 * so['win']:.1f}% {so['net']:>+9,.0f} PF {so['pf']:.2f} | coin p {pc:.4f} | "
              f"{'KEEP' if ok_ else ('better in both, fails the coin flip' if better else 'NOT better in both')}", flush=True)
    print(f"\n  {'KEEP: ' + '; '.join(kept) if kept else 'Nothing passes: order flow (tick-rule proxy) does not improve the rule.'}")


if __name__ == "__main__":
    main()
