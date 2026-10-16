#!/usr/bin/env python3
"""decay_clock_check.py - which clock do REAL option prices decay on intraday? (14 Oct 2026, for stock_options_study.py)

For each recorded day and index (~/trading-tool-logs/option_history, run on the VM), the at-the-money straddle's implied
volatility at 09:30 and at 15:15, backed out two ways - time to expiry in calendar hours (what every study's
pro_study.years_to uses) and in trading hours. Whichever clock the market prices on gives a volatility that stays level.
RESULT (14 Oct 2026, 53 index-days, expiry day and the day before left out): median change -2.7% on the calendar clock,
+3.1% on the trading clock. BUT this does NOT carry over to the tool's own trades: its 183 rules trades on 22 recorded
days, priced from each entry's own implied volatility, came out per lot REAL +66,777 / calendar model +57,354 / blend
model +41,017 - the calendar clock the studies use is slightly CAUTIOUS (-51 a trade), the blend too harsh (-141).
The 3-year studies stand on the calendar clock.
Read-only; places nothing.    ~/nbs/.venv/bin/python decay_clock_check.py
"""
import bisect, datetime as dt, glob, math, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.expanduser("~/nbs"))
import regime_study as rs
files = sorted(glob.glob(os.path.expanduser("~/trading-tool-logs/option_history/2026-*.csv.gz")))
days = [dt.date.fromisoformat(os.path.basename(f)[:10]) for f in files]
def iv(px, S, K, T):
    lo, hi = 0.005, 3.0
    for _ in range(70):
        m = (lo + hi) / 2
        if rs.bs(S, K, T, m, True) + rs.bs(S, K, T, m, False) > px: hi = m
        else: lo = m
    return (lo + hi) / 2
def T_cal(exp, t):
    return max((pd.Timestamp(dt.datetime.combine(exp, dt.time(15, 30))) - t).total_seconds() / (365 * 86400), 1e-6)
def T_trd(exp, t):
    d = t.date()
    left = max(0.0, (pd.Timestamp(dt.datetime.combine(d, dt.time(15, 30))) - t).total_seconds()) / (6.25 * 3600)
    n = int(np.busday_count(d + dt.timedelta(days=1), exp + dt.timedelta(days=1)))
    return max((n + left) / 252, 1e-6)
rows = []
for f, d in zip(files, days):
    x = pd.read_csv(f)
    if len(x) < 100: continue
    x["ts"] = pd.to_datetime(x["ts"]); x["ts"] = x["ts"].dt.tz_localize(None) if x["ts"].dt.tz is not None else x["ts"]
    for key in x.loc[x["kind"] == "OPT", "index"].unique():
        idx = x[(x["kind"] == "IDX") & (x["index"] == key)].set_index("ts")["close"]
        opt = x[(x["kind"] == "OPT") & (x["index"] == key)]
        exp = sorted(opt["expiry"].unique())[0]
        e = dt.date.fromisoformat(str(exp)[:10])
        if e <= d + dt.timedelta(days=1):      # skip expiry day and the day before (the decay is extreme there)
            continue
        opt = opt[opt["expiry"] == exp]
        t0, t1 = pd.Timestamp(dt.datetime.combine(d, dt.time(9, 30))), pd.Timestamp(dt.datetime.combine(d, dt.time(15, 15)))
        s0 = idx[idx.index <= t0]
        if not len(s0): continue
        S0 = float(s0.iloc[-1])
        ks = sorted(opt["strike"].unique()); K = min(ks, key=lambda k: abs(k - S0))
        out = {"day": d, "index": key}
        ok = True
        for tag, t in (("open", t0), ("close", t1)):
            S = idx[idx.index <= t]
            c = opt[(opt["strike"] == K) & (opt["opt"] == "CE") & (opt["ts"] <= t)]
            p = opt[(opt["strike"] == K) & (opt["opt"] == "PE") & (opt["ts"] <= t)]
            if not len(S) or not len(c) or not len(p): ok = False; break
            St, px = float(S.iloc[-1]), float(c["close"].iloc[-1]) + float(p["close"].iloc[-1])
            out[f"cal_{tag}"] = iv(px, St, K, T_cal(e, t)); out[f"trd_{tag}"] = iv(px, St, K, T_trd(e, t))
        if ok: rows.append(out)
r = pd.DataFrame(rows)
r["cal_chg"] = r["cal_close"] / r["cal_open"] - 1; r["trd_chg"] = r["trd_close"] / r["trd_open"] - 1
print(len(r), "index-days (expiry day and the day before left out)")
print(r.groupby("index")[["cal_chg", "trd_chg"]].median().round(3).to_string())
print("ALL median change 09:30 -> 15:15 in implied vol:  calendar clock", round(r.cal_chg.median() * 100, 1), "%   trading clock", round(r.trd_chg.median() * 100, 1), "%")
print("mean:", round(r.cal_chg.mean() * 100, 1), "% /", round(r.trd_chg.mean() * 100, 1), "%")
