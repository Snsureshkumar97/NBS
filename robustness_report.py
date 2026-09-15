#!/usr/bin/env python3
"""
robustness_report.py — does the live result survive the checks a sceptic would run?
==================================================================================
PRE-DECLARED 15 Sep 2026, before this was run. Adapted from the "stress test"
step of ajeeshworkspace/indian-trading-skills (backtest-expert). Nothing here
changes a rule; it only says how much weight the backtest can bear.

The rules measured are the ones live from 15 Sep 2026 (wait for the 09:15-09:45
range, no break, T3 at least 1x the stop, all three indices, exit at T2), priced
as pro_study.py prices them: real expiries, per lot, Zerodha's charges, 0.25%
slippage a side. Shown for all three indices and for Nifty + Sensex alone.

  1. Year by year - three 12-month windows from the start of the history. Is one
     year carrying the whole result?
  2. Remove the best month, and separately the best five days. Still positive?
  3. Is the average trade distinguishable from zero? A t-test on per-trade net
     (trades overlap in time, so this flatters it), and a bootstrap that
     resamples whole trading days - 2,000 draws - for a 95% range of the average.
  4. Expiry days against every other day, with the same test.
  5. Slippage at 0% and 0.5% a side, the same trades repriced.

    python3 robustness_report.py

RESULT, history to 11 Sep 2026 (per lot, after costs):

  ALL THREE INDICES (the rules live today)
    first two years  3,305 trades  +339,871  t 2.08 (p 0.04)  day-bootstrap avg -115..+317
    held-out year    2,436 trades    +5,524  t 0.04 (p 0.97)  day-bootstrap avg -263..+273
    three years      5,741 trades  +345,394  t 1.57 (p 0.12)  day-bootstrap avg -108..+224
    by year: -71,080 | +317,015 | +99,460  (Bank Nifty -43,998 | +6,359 | -181,068)
    without the best 5 days, three years: -54,131; held-out without its best month: -235,041
    expiry days +553,589 (t 5.38); every other day -208,195
    slippage 0.5% a side: held-out -129,385
  -> Not distinguishable from zero. One good year, a handful of days and expiry
     day carry it; every 95% range of the average trade includes zero.

  NIFTY + SENSEX ONLY
    three years      3,820 trades  +564,102  t 3.25 (p 0.001) day-bootstrap avg -30..+331
    held-out year    1,663 trades  +223,182  t 1.86 (p 0.06)
    by year: -27,082 | +310,656 | +280,528
    without the best 5 days, three years: +248,443; held-out without its best month: +24,729
    expiry days +502,831 (t 5.54); every other day +61,271 (t 0.42)
    slippage 0.5% a side: held-out +162,142
  -> Sturdier - positive two years of three, survives losing its best days over
     three years and survives doubled slippage - but the day-bootstrap range still
     just includes zero and the profit is expiry-day profit.

  Caveats: trades overlap, so the t-test flatters; the prices are modelled; and
  several rules were chosen after the held-out year had been seen.
"""
import collections
import json
import math
import os
import sys

import numpy as np
import pandas as pd

import backtest_intraday as bt
import pro_study as ps
import regime_study as rs

INDICES = rs.INDICES
rng = np.random.default_rng(15092026)


def rr_ok(r):
    return bool(r.get("risk_points") and r["index_targets"][2] is not None
                and abs(r["index_targets"][2] - r["spot"]) >= r["risk_points"])


def collect():
    """Every trade on the live rules, kept with what it takes to reprice it."""
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    trades = []
    for k in INDICES:
        df = hists[k]
        F = rs.features(df, vix, nrv)
        ready = F["or_ready"].to_numpy()
        A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
             "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
             "days": set(df.index.date)}
        res = bt.run(k, df, gate=lambda i, r, ready=ready: bool(ready[i]) and rr_ok(r))
        pos = {t: n for n, t in enumerate(df.index)}
        sig = F["sigma"].to_numpy()
        for tr in res["trades"]:
            i = pos[tr["when"]]
            s = sig[i]
            if not (s == s) or s <= 0:
                continue
            legs = ps.simulate(A, i, tr)
            p = ps.price(k, df, A, i, tr, legs, s)
            if p:
                trades.append({"k": k, "df": df, "A": A, "i": i, "tr": tr, "legs": legs, "s": s, **p})
    return trades


def tstat(x):
    x = np.asarray(x, float)
    if len(x) < 3 or x.std(ddof=1) == 0:
        return None, None
    t = x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))
    return t, math.erfc(abs(t) / math.sqrt(2))          # two-sided, normal approximation


def day_bootstrap(rows, draws=2000):
    """95% range of the average trade, resampling whole trading days."""
    by_day = collections.defaultdict(list)
    for r in rows:
        by_day[r["when"].date()].append(r["net"])
    days = list(by_day.values())
    if len(days) < 10:
        return None
    means = []
    for _ in range(draws):
        pick = rng.integers(0, len(days), len(days))
        nets = [v for j in pick for v in days[j]]
        means.append(np.mean(nets))
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def summary(rows):
    if not rows:
        return None
    st = ps.stats(rows)
    t, p = tstat([r["net"] for r in rows])
    boot = day_bootstrap(rows)
    return {"n": st["n"], "total": round(st["total"]), "avg": round(st["avg"]), "pf": round(st["pf"], 2),
            "dd": round(st["dd"]), "t": None if t is None else round(t, 2), "p": None if p is None else round(p, 4),
            "avg_95": None if boot is None else [round(boot[0]), round(boot[1])]}


def without_best(rows, key):
    groups = collections.defaultdict(float)
    for r in rows:
        groups[key(r)] += r["net"]
    return groups


def main():
    trades = collect()
    scopes = {"all three": lambda r: True, "Nifty + Sensex": lambda r: r["index"] != "BANKNIFTY"}
    periods = {"first two years": lambda r: r["when"] < rs.SPLIT, "held-out year": lambda r: r["when"] >= rs.SPLIT,
               "three years": lambda r: True}
    start = min(r["when"] for r in trades)
    windows = [(start + pd.DateOffset(years=y), start + pd.DateOffset(years=y + 1)) for y in range(3)]
    out = {"rules": "live from 15 Sep 2026", "trades": len(trades), "scopes": {}}
    for sname, keep in scopes.items():
        rows = [r for r in trades if keep(r)]
        sc = {"periods": {p: summary([r for r in rows if f(r)]) for p, f in periods.items()}}
        sc["years"] = []
        for a, b in windows:
            yr = [r for r in rows if a <= r["when"] < b]
            s_ = summary(yr) if yr else None
            sc["years"].append({"from": str(a.date()), "to": str((b - pd.Timedelta(days=1)).date()), **(s_ or {})})
        sc["by_index_years"] = {k: [round(sum(r["net"] for r in rows if r["index"] == k and a <= r["when"] < b))
                                    for a, b in windows] for k in INDICES if any(r["index"] == k for r in rows)}
        for label, f in periods.items():
            part = [r for r in rows if f(r)]
            total = sum(r["net"] for r in part)
            months = without_best(part, lambda r: (r["when"].year, r["when"].month))
            days = without_best(part, lambda r: r["when"].date())
            best_m = max(months.items(), key=lambda kv: kv[1])
            best5 = sorted(days.values(), reverse=True)[:5]
            sc.setdefault("remove_best", {})[label] = {
                "total": round(total), "best_month": f"{best_m[0][0]}-{best_m[0][1]:02d}",
                "best_month_net": round(best_m[1]), "without_best_month": round(total - best_m[1]),
                "best_5_days_net": round(sum(best5)), "without_best_5_days": round(total - sum(best5))}
            sc.setdefault("expiry", {})[label] = {"expiry": summary([r for r in part if r["expiry_day"]]),
                                                  "other": summary([r for r in part if not r["expiry_day"]])}
        out["scopes"][sname] = sc
    # 5. slippage: reprice the same trades
    old = ps.SLIP
    slip = {}
    for s_ in (0.0, 0.0025, 0.005):
        ps.SLIP = s_
        repriced = []
        for r in trades:
            p = ps.price(r["k"], r["df"], r["A"], r["i"], r["tr"], r["legs"], r["s"])
            if p:
                repriced.append(p)
        slip[f"{s_*100:.2f}%"] = {sn: {pn: round(sum(x["net"] for x in repriced if keep(x) and f(x)))
                                        for pn, f in periods.items() if pn != "three years"}
                                  for sn, keep in scopes.items()}
    ps.SLIP = old
    out["slippage"] = slip
    return out


if __name__ == "__main__":
    res = main()
    print(json.dumps(res, indent=1, default=str))
    if len(sys.argv) > 1:
        json.dump(res, open(sys.argv[1], "w"), indent=1, default=str)
