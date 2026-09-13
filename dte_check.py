"""MACD against the assumption that actually dominates: days to expiry.

regime_study moved the same trades from +497,265 to -155,040 purely on how far
from expiry they were priced. Slippage barely registers beside that. So the
survivor is re-scored with the expiry pushed out - if the advantage only exists
at the near expiry, it belongs to the assumption.

price() takes the real contract from the exchange calendar, so "further out" is
forced by asking for the NEXT expiry after the one it would use.
"""
import math
import numpy as np, pandas as pd
import backtest_intraday as bt, regime_study as rs, pro_study as ps
import strategy_study as ss

INDICES, SPLIT = ps.INDICES, rs.SPLIT
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

def score(gate_for, next_exp):
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
            p = ps.price(k, df, A[k], i, tr, legs, sg,
                         next_expiry_on_expiry_day=next_exp)
            if p:
                rows.append(p)
    return ({"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
             "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}, rows)

print(f"{'pricing':<26} {'in-sample vs live':>24} {'held-out vs live':>24}")
print("-" * 78)
held = []
for next_exp, label in ((False, "nearest expiry (default)"), (True, "next expiry on expiry day")):
    live, _ = score(lambda k: base[k], next_exp)
    r, rows = score(lambda k: gates[k]["MACD agrees"], next_exp)
    di = r["is"]["total"] - live["is"]["total"]
    do = r["oos"]["total"] - live["oos"]["total"]
    held.append(di > 0 and do > 0)
    print(f"{label:<26} {di:>+17,.0f} PF {r['is']['pf']:.2f} {do:>+17,.0f} PF {r['oos']['pf']:.2f}")

print("\n" + "-" * 78)
print(f"  MACD beat the live rules in both periods under "
      f"{sum(held)} of {len(held)} expiry assumptions")

# Where does the gain come from - broad, or a few avoided disasters?
live, lrows = score(lambda k: base[k], False)
r, mrows = score(lambda k: gates[k]["MACD agrees"], False)
lk = {(x["index"], x["when"]) for x in mrows}
removed = [x for x in lrows if (x["index"], x["when"]) not in lk]
nets = np.array([x["net"] for x in removed])
print(f"\n  trades MACD removed: {len(removed):,} of {len(lrows):,}")
print(f"    their total P&L    : {nets.sum():>+12,.0f}  (this is what skipping them saved)")
print(f"    were losers        : {(nets < 0).mean()*100:.1f}%  (live rules overall: "
      f"{np.mean([x['net'] < 0 for x in lrows])*100:.1f}%)")
worst = np.sort(nets)[:10]
print(f"    ten worst removed  : {', '.join(f'{v:,.0f}' for v in worst)}")
print(f"    top 10 removed losses are {abs(worst.sum())/abs(nets[nets<0].sum())*100:.0f}% "
      f"of all the losses it avoided")
