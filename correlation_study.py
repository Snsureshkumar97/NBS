#!/usr/bin/env python3
"""correlation_study.py - one ticket per direction across the indices.

PRE-DECLARED 15 Sep 2026, before running - from TradingAgents' risk-manager role.
Nifty, Bank Nifty and Sensex move together, so tickets on two or three of them in the same direction
at once are one bet taken several times. Variant: no new ticket while another index already holds an
open ticket in the SAME direction (CE with CE, PE with PE). Applied on the merged timeline of the live
rules (range wait, no break, T3 >= 1x stop, no RSI divergence, all three indices, exit at T2), priced
as pro_study prices them. Kept only if better than the live rules in BOTH periods. Note: a skipped
trade does not free that index's own gap for a later entry, so the variant is slightly conservative.

RESULT, history to 11 Sep 2026, per lot after costs:
                       live rules                  one ticket per direction
  first two years      3,233 trades  +359,660      1,583 trades  +126,273  PF 1.07
  held-out year        2,380 trades   +34,528      1,019 trades   -29,632  PF 0.98
The trades it held back made +233,387 and +64,160: less in both periods. Not used.

    python3 correlation_study.py [out.json]
"""
import sys, math, json
import numpy as np, pandas as pd
import backtest_intraday as bt, regime_study as rs, pro_study as ps, indicators as ind, skills_study as ss
allh = {k: bt.fetch_history(k, years=3, use_cache=True) for k in rs.INDICES}
vix = rs.load_vix()
nrv = np.log(rs.daily_close(allh["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
rows = []
for k in rs.INDICES:
    df = allh[k]; F = rs.features(df, vix, nrv); ready = F["or_ready"].to_numpy()
    c = df["Close"].to_numpy(); rsi = ind.rsi(df["Close"], 14).to_numpy()
    A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": c,
         "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(), "days": set(df.index.date)}
    gate = lambda i, r, ready=ready, c=c, rsi=rsi: bool(ready[i]) and ss.rr_ok(r) and not ss.divergence(c, rsi, i, ps._side(r))
    res = bt.run(k, df, gate=gate)
    pos = {t: n for n, t in enumerate(df.index)}; sig = F["sigma"].to_numpy()
    for tr in res["trades"]:
        i = pos[tr["when"]]; s = sig[i]
        if not (s == s) or s <= 0: continue
        p = ps.price(k, df, A, i, tr, ps.simulate(A, i, tr), s)
        if p: p["side"] = tr["side"]; rows.append(p)
rows.sort(key=lambda r: r["when"])
kept, skipped = [], []
for r in rows:
    clash = any(o["index"] != r["index"] and o["side"] == r["side"] and o["when"] < r["when"] < o["exit_time"] for o in kept[-40:])
    (skipped if clash else kept).append(r)
def st(xs):
    s = ps.stats(xs)
    return None if not s else {"n": s["n"], "total": round(s["total"]), "avg": round(s["avg"]), "pf": round(s["pf"], 2), "dd": round(s["dd"]), "win": round(s["win"], 1)}
out = {}
for per, f in (("in_sample", lambda r: r["when"] < rs.SPLIT), ("held_out", lambda r: r["when"] >= rs.SPLIT)):
    out[per] = {"live": st([r for r in rows if f(r)]), "cap": st([r for r in kept if f(r)]),
                "skipped": st([r for r in skipped if f(r)]),
                "live N+S": st([r for r in rows if f(r) and r["index"] != "BANKNIFTY"]),
                "cap N+S": st([r for r in kept if f(r) and r["index"] != "BANKNIFTY"])}
di = out["in_sample"]["cap"]["total"] - out["in_sample"]["live"]["total"]
do = out["held_out"]["cap"]["total"] - out["held_out"]["live"]["total"]
out["verdict"] = {"in_sample_vs_live": di, "held_out_vs_live": do, "keep": di > 0 and do > 0}
print(json.dumps(out, indent=1))
if len(sys.argv) > 1:
    json.dump(out, open(sys.argv[1], "w"), indent=1)
