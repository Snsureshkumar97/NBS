#!/usr/bin/env python3
"""Re-measure the rule AS IT NOW RUNS, not as the study expressed it.

strategy_study applied "MACD agrees" as a gate OUTSIDE the engine: the engine
proposed a signal, the gate rejected it, and the slot was already spent. The
live rule is a veto INSIDE the engine, so a blocked signal frees the spacing
and a later bar can take its place.

Same intent, different trade sequence: 1,248 vs 1,253 trades on Nifty, only
1,048 shared. So the figures the study produced do not describe what runs, and
config.MACD_MUST_AGREE must carry numbers measured on the veto itself.

Pre-declared, unchanged from before: kept only if it beats the live rules in
BOTH periods after costs. If the veto fails where the gate passed, it is
reported failed and MACD_MUST_AGREE goes back to False.
"""
import math
import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import pro_study as ps
import regime_study as rs

INDICES, SPLIT = ps.INDICES, rs.SPLIT


def main():
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = (np.log(rs.daily_close(hists["NIFTY"])).diff()
           .rolling(20).std() * math.sqrt(252))
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    A = {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(),
                "cl": df["Close"].to_numpy(),
                "end": (pd.Series(df.index.date)
                        != pd.Series(df.index.date).shift(-1)).to_numpy(),
                "days": set(df.index.date)}
    base = {k: rs.make_gates(F[k])["opening-range break"] for k in INDICES}

    def score():
        rows = []
        for k in INDICES:
            df = hists[k]
            res = bt.run(k, df, gate=base[k])
            pos = {t: n for n, t in enumerate(df.index)}
            sig = F[k]["sigma"].to_numpy()
            for tr in res["trades"]:
                i = pos[tr["when"]]
                sg = sig[i]
                if not (sg == sg) or sg <= 0:
                    continue
                legs = ps.simulate(A[k], i, tr)
                p = ps.price(k, df, A[k], i, tr, legs, sg)
                if p:
                    rows.append(p)
        return {"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
                "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}

    out = {}
    for flag in (False, True):
        config.MACD_MUST_AGREE = flag
        out[flag] = score()
    config.MACD_MUST_AGREE = True

    print("\n" + "=" * 86)
    print(" The MACD veto as it actually runs (inside the engine), per lot after costs")
    print("=" * 86)
    print(f" {'rule':<26} {'IN-SAMPLE n, net, PF':>27}  | {'HELD-OUT n, net, PF':>25}")
    print("-" * 86)
    for flag, label in ((False, "live rules (veto off)"), (True, "MACD must agree (veto on)")):
        r = out[flag]
        i, o = r["is"], r["oos"]
        print(f" {label:<26} {i['n']:>5} ₹{i['total']:>+11,.0f} PF {i['pf']:.2f}"
              f"  | {o['n']:>5} ₹{o['total']:>+11,.0f} PF {o['pf']:.2f}")
    di = out[True]["is"]["total"] - out[False]["is"]["total"]
    do = out[True]["oos"]["total"] - out[False]["oos"]["total"]
    print("-" * 86)
    print(f" difference{'':<17} {di:>+16,.0f}       {do:>+18,.0f}")
    ok = di > 0 and do > 0
    print()
    print(" VERDICT:", "HOLDS as a veto - the config figures can describe it"
          if ok else "FAILS as a veto - MACD_MUST_AGREE must go back to False")
    return out, ok


if __name__ == "__main__":
    main()
