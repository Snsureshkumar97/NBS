"""Do the two survivors hold up when the ASSUMPTIONS move?

17 candidates were tested. Two passing both periods is about what luck alone
delivers, so surviving the split is necessary and nowhere near sufficient.

The assumption sweep already dominates everything in regime_study: the same
trades ran +497,265 priced as expiry-day options and -155,040 priced six days
out. If MACD's advantage over the live rules survives only at one pricing
assumption, it is a property of that assumption, not an edge.

Tested here, each against the live rules under the SAME assumption:
    slippage   0.25% a side (default), 0% , 0.50%
Pre-declared: a survivor is believed only if it beats the live rules in BOTH
periods at EVERY slippage. Anything less is reported as not proven.
"""
import math
import numpy as np
import pandas as pd
import backtest_intraday as bt
import config, regime_study as rs, pro_study as ps
import strategy_study as ss

INDICES = ps.INDICES
SPLIT = rs.SPLIT

hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
vix = rs.load_vix()
nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
A = {}
for k, df in hists.items():
    A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(),
            "cl": df["Close"].to_numpy(),
            "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
            "days": set(df.index.date)}
base = {k: rs.make_gates(F[k])["opening-range break"] for k in INDICES}
gates = {k: ss.build_gates(hists[k], base[k]) for k in INDICES}

def score(gate_for, slip):
    old = ps.SLIP
    ps.SLIP = slip                     # price() reads the module-level SLIP
    try:
        rows = []
        for k in INDICES:
            df = hists[k]
            res = bt.run(k, df, gate=gate_for(k))
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
        return ({"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
                 "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])})
    finally:
        ps.SLIP = old

CANDIDATES = ["MACD agrees", "EMA + MACD agree"]
print(f"{'slippage':>10} {'candidate':<22} {'in-sample vs live':>22} {'held-out vs live':>22}")
print("-" * 82)
verdict = {c: [] for c in CANDIDATES}
for slip, label in ((0.0, "none"), (0.0025, "0.25% (default)"), (0.005, "0.50%")):
    live = score(lambda k: base[k], slip)
    for c in CANDIDATES:
        r = score(lambda k, n=c: gates[k][n], slip)
        di = r["is"]["total"] - live["is"]["total"]
        do = r["oos"]["total"] - live["oos"]["total"]
        verdict[c].append(di > 0 and do > 0)
        print(f"{label:>10} {c:<22} {di:>+15,.0f} PF {r['is']['pf']:.2f} "
              f"{do:>+15,.0f} PF {r['oos']['pf']:.2f}")
    print("-" * 82)

print()
for c in CANDIDATES:
    held = all(verdict[c])
    print(f"  {'HOLDS' if held else 'NOT PROVEN':<11} {c}: beat the live rules in both periods at "
          f"{sum(verdict[c])} of {len(verdict[c])} slippage assumptions")
