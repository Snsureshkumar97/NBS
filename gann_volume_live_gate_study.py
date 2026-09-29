#!/usr/bin/env python3
"""
gann_volume_live_gate_study.py — does Gann/volume help TODAY's real entries, not an old baseline?
================================================================================
The user, 29 Sep 2026: "check gann volume study, does that help either" (after the candle-pattern
filter was dropped for the same reason - see candle_pattern_study.py).

WHY gann_volume_study.py'S OWN NUMBER IS STALE
    gann_volume_study.py measures against `regime_study.make_gates(...)["opening-range break"]` -
    pro_study.py's own "LIVE RULES" row, which its own module docstring already flags as predating
    changes since adopted (skills_study.py's RSI-divergence filter, the trend-day room, REGIME_OR_
    BREAK switched off) - see reversal_exit_study.py's docstring, which moved away from this exact
    approximation for exactly this reason. The trade counts prove the drift: gann_volume_study.py's
    baseline took 2,521 in-sample trades; tickets.py's REAL gates (_regime_hold / _divergence_hold /
    _reward_hold / _spread_hold, called directly) take 3,627 on the identical 3-year history. A study
    measured against the wrong baseline can only answer "does this help an entry logic the tool no
    longer runs" - not the question actually asked.

WHAT THIS DOES DIFFERENTLY
    Same Gann-level and volume-oscillator gates, unchanged, straight from gann_volume_study.py
    (g1_room, g2_behind, v1_rising - not reimplemented, so nothing about the indicators themselves is
    retested here). Only the BASELINE changes: reversal_exit_study.live_gate, today's real gates,
    the same one candle_pattern_study.py and reversal_exit_study.py both use. Same exit (hold to
    T2/stop), same real-expiry pricing, same after-costs stats(), same in-sample/held-out split.

    python3 gann_volume_live_gate_study.py
"""
import math

import numpy as np
import pandas as pd

import backtest_intraday as bt
import gann_volume_study as gv
import pro_study as ps
import regime_study as rs
import reversal_exit_study as res

INDICES = ps.INDICES
SPLIT = ps.SPLIT


def study_indices():
    print("Loading history (shared cache)...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    X = {k: gv.features(hists[k]) for k in INDICES}
    A = {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
                "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
                "days": set(df.index.date)}
    base_gate = {k: (lambda i, rec, k=k: res.live_gate(k, i, rec, hists[k])) for k in INDICES}

    def evaluate(extra):
        rows = []
        n_raw = 0
        for k in INDICES:
            df = hists[k]
            g = extra(X[k]) if extra else None
            gate = (lambda i, r, k=k, g=g: base_gate[k](i, r) and (g is None or g(i, r)))
            result = bt.run(k, df, gate=gate)
            n_raw += len(result["trades"])
            pos = {t: n for n, t in enumerate(df.index)}
            sig = F[k]["sigma"].to_numpy()
            for tr in result["trades"]:
                i = pos[tr["when"]]
                s = sig[i]
                if not (s == s) or s <= 0:
                    continue
                legs = ps.simulate(A[k], i, tr)
                p = ps.price(k, df, A[k], i, tr, legs, s)
                if p:
                    rows.append(p)
        return {"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
                "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}, n_raw

    def line(name, r):
        a, b = r["is"], r["oos"]
        return (f" {name:28s} {a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    print("=" * 132)
    print(" All per lot, pooled across NIFTY / BANKNIFTY / SENSEX, real expiries, after costs")
    print(" Baseline: TODAY'S REAL live gate (tickets.py's own _regime_hold/_divergence_hold/")
    print(" _reward_hold/_spread_hold), not pro_study.py's older 'opening-range break' approximation.")
    print("=" * 132)
    base, base_raw = evaluate(None)
    print(line("today's live gate (baseline)", base))
    out = {}
    for name, fn in gv.VARIANTS.items():
        if name.startswith("V1"):
            if all(X[k]["vo"] is None for k in INDICES):
                print(f" {name:28s} not measured: the index history carries no volume")
                continue
        r, raw = evaluate(fn)
        out[name] = r
        kept = 100 * raw / base_raw if base_raw else 0.0
        print(line("live + " + name, r) + f"   (kept {raw}/{base_raw} = {kept:4.1f}%)")

    print("\n VERDICT — kept only if better than today's real baseline both in-sample and held-out")
    for name, r in out.items():
        if not (r["is"] and r["oos"] and base["is"] and base["oos"]):
            print(f"   drop  {name:28s} too few trades to compare")
            continue
        di = r["is"]["total"] - base["is"]["total"]
        do = r["oos"]["total"] - base["oos"]["total"]
        dd = r["oos"]["dd"] - base["oos"]["dd"]
        print(f"   {'KEEP ' if di > 0 and do > 0 else 'drop '} {name:28s} in-sample {di:>+10,.0f}   "
              f"held-out {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")
    return base, out


if __name__ == "__main__":
    study_indices()
