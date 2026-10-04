#!/usr/bin/env python3
"""
cfd_scale_out_study.py — scale out a third at T1, a third at T2, the rest at T3 (both Exness rules)
================================================================================
The user, 4 Oct 2026: "we have three targets so when the price reach target 1 exit 33% when t2 exit the remaining
33% and target 3 remaning everything what will be the result". The stop does NOT move (as the rules have it), so
each third is its own trade with the same entry and stop and its own target: on one price path the position's
result is exactly the average of the three, and it is fully out when its LAST third is (the next trade waits
for that). Each third pays its own exit spread and its own nights of swap. Same signals as live (BTC's spread
check on its full target). BTC from real ticks (cfd_target_sweep's walk at T1/T2/T3 = 1/3, 2/3, all of 0.2 x
stop), gold from the real-tick outcome tables (T1/T2/T3 = 0.25/0.5/0.75 x stop).

    python3 cfd_scale_out_study.py
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

COOLDOWN_NS = 20 * 60 * 10 ** 9


def seq(cands, long_sw, rolls, cumw, mult, mode):
    """cands: (t0, side, [(net pts, exit ns) per third]) in time order -> list of (t0, $ per lot)."""
    out, free = [], -1
    for t0, s, thirds in cands:
        if t0 < free:
            continue
        use = thirds if mode == "scale" else [thirds[-1]]                 # "all at T3" = the live single exit
        vals = []
        for pts, t1 in use:
            nights = cumw[np.searchsorted(rolls, t1, side="right") - 1] - cumw[np.searchsorted(rolls, t0, side="right") - 1]
            vals.append(pts + (nights * long_sw / mult if s > 0 else 0.0))
        out.append((t0, float(np.mean(vals)) * mult))
        free = max(t1 for _, t1 in use) + COOLDOWN_NS
    return out


def report(name, rows, split, lot_label):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_vote_search as vs
    cells, tot = [], 0.0
    for per in ("is", "oos"):
        x = np.array([v for t0, v in rows if (t0 < split) == (per == "is")])
        st = vs.stats(x)
        tot += st["net"]
        cells.append(f"{st['n']:>5} {100 * st['win']:>5.1f}% {st['net']:>+9,.0f} {st['pf']:>5.2f} {st['dd']:>7,.0f}")
    eq = np.cumsum([0.25 * v for _, v in rows])
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq)) if len(eq) else 0.0
    print(f"  {name:30s} {cells[0]:>46s}   {cells[1]:>46s}   {0.25 * tot:>+10,.0f} {dd:>9,.0f}")


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_rules
    import cfd_target_sweep as ts
    import cfd_tick_study as cts
    import cfd_vote_search as vs
    import config
    import exness_data as ed
    head = (f"  {'exit':30s} {'IN-SAMPLE   n   win%      net    PF   maxDD':>46s}   {'HELD-OUT   n   win%      net    PF   maxDD':>46s}"
            f"   {'0.25 lot 3y':>10s} {'worst drop':>9s}")
    # ---- BTC: every signal walked on ticks at the three targets
    full = ed.load("BTCUSD")
    a = cfd_rules.compute(full[["Open", "High", "Low", "Close", "Volume"]])
    side = np.where((a["rsi2"] != 0) & a["adx25"], a["rsi2"], 0)
    close_ns = full.index.tz_convert("UTC").asi8 + 15 * 60 * 10 ** 9
    by_month = {}
    for i in np.flatnonzero(side != 0):
        ts_ = pd.Timestamp(int(close_ns[i]), tz="UTC")
        by_month.setdefault((ts_.year, ts_.month), []).append(
            (int(close_ns[i]), int(side[i]), float(full["Close"].iloc[i]), float(3.0 * a["atr"][i]), float(full["spread_close"].iloc[i])))
    jobs = [(y, m, by_month.get((y, m), []), ts.LADDER) for (y, m) in ed.months_until(cts.LAST)]
    walked = []
    with ProcessPoolExecutor(max(1, min(6, (os.cpu_count() or 2) - 1))) as ex:
        for got in ex.map(ts._walk, jobs):
            walked.extend(got)
    walked.sort(key=lambda r: r[0])
    cands = [(t0, s, res) for t0, s, R, spc, res in walked if max(spc, ts.FLOOR) < 0.2 * 0.2 * R]   # the live spread check (T3)
    long_sw, _, triple = cts.SWAP["BTCUSD"]
    rolls, cumw = vs.rollover_weights(cands[0][0], max(r[2][-1][1] for r in cands) + 3 * 86400 * 10 ** 9, triple)
    print("BTC (RSI-2 rule) - $ per BTC; T1/T2/T3 = 0.067/0.133/0.2 x the 3-ATR stop")
    print(head)
    report("all at T3 (live)", seq(cands, long_sw, rolls, cumw, 1.0, "t3"), vs.SPLIT, "BTC")
    report("a third at T1, T2, T3", seq(cands, long_sw, rolls, cumw, 1.0, "scale"), vs.SPLIT, "BTC")
    # ---- GOLD: the outcome tables at 0.25 / 0.5 / 0.75
    plan = config.CFD_RULES["GOLD"]
    fg = ed.load("XAUUSD")
    ag = cfd_rules.compute(fg[["Open", "High", "Low", "Close", "Volume"]])
    vv = np.stack([ag[v] for v in plan["votes"]]).astype(np.int64)
    gs = np.where(np.all(vv == vv[0], axis=0) & (vv[0] != 0), vv[0], 0)
    for f in plan["filters"]:
        gs = np.where(ag[f], gs, 0)
    spc = fg["spread_close"].to_numpy(); eff = np.maximum(spc, 0.26)
    ent = fg.index.tz_convert("UTC").asi8 + 15 * 60 * 10 ** 9
    tabs = []
    for tgt, table in ((0.25, "outcomes_close"), (0.5, "outcomes"), (0.75, "outcomes")):
        O = np.load(os.path.join(ed._dir(), f"{table}_XAUUSD.npz"))
        k = list(O["stops"]).index(3.0); j = int(np.argmin(np.abs(O["targets"] - tgt)))
        tabs.append((O["net"][:, :, k, j].astype(float) - (eff - spc)[:, None], O["ext"][:, :, k, j], O["ok"]))
    gc = []
    for i in np.flatnonzero(gs != 0):
        sd = 0 if gs[i] > 0 else 1
        thirds = [(n[i, sd], e[i, sd]) for n, e, ok in tabs]
        if all(ok[i] for _, _, ok in tabs) and not any(np.isnan(p) for p, _ in thirds):
            gc.append((int(ent[i]), int(gs[i]), thirds))
    gl, _, gtri = cts.SWAP["XAUUSD"]
    grolls, gcumw = vs.rollover_weights(ent[0], ent[-1] + 3 * 86400 * 10 ** 9, gtri)
    print("\nGOLD (trend + momentum rule) - $ per lot (100 oz); T1/T2/T3 = 0.25/0.5/0.75 x the 3-ATR stop")
    print(head)
    report("all at T3 (live)", seq(gc, gl, grolls, gcumw, 100.0, "t3"), vs.SPLIT, "GOLD")
    report("a third at T1, T2, T3", seq(gc, gl, grolls, gcumw, 100.0, "scale"), vs.SPLIT, "GOLD")


if __name__ == "__main__":
    main()
