#!/usr/bin/env python3
"""
drawdown_controls_study.py — can a risk layer cut the drawdown without cutting the edge?
=======================================================================================
PRE-DECLARED 16 Sep 2026, before this was run. Two controls from projects that
work on drawdowns, each judged against the rules live today (wait for the range,
no break, T3 >= 1x the stop with more room on a trend day, no entry into an RSI
divergence, all three indices, exit at T2), priced as pro_study.py prices them:
real expiries, per lot, Zerodha's charges, 0.25% slippage, drawdowns walked in
time order from zero.

  A. Drawdown circuit breaker (SilentFleetKK/riskguard). The strategy's own
     record of every trade it signals - the "shadow" curve - is kept whether or
     not a trade is taken. When the shadow curve is 1,00,000 per lot below its
     high, new trades are paper only; real trading resumes once it is back within
     50,000 of that high. Only trades already closed at the moment of an entry
     count, so nothing is known before it happened.
  B. Half size when volatility is high (robcarver17/pysystemtrade's volatility
     targeting, one-sided). A trade is taken at half size when yesterday's India
     VIX was above the 75th percentile of the year before it; full size otherwise.
     Never sized up. Half a lot is not tradeable, so this is the expected value.

A control is kept only if, in BOTH the first two years and the held-out year, it
LOWERS the worst drawdown AND RAISES profit / worst drawdown - so it cannot pass
merely by making every number smaller. One setting each, not swept.

    python3 drawdown_controls_study.py [out.json]

RESULT, history to 11 Sep 2026, per lot after costs, all three indices:
                     first two years                      held-out year
                     profit    worst DD  profit/DD        profit    worst DD  profit/DD
  live rules         +629,112  174,098   3.61             +144,028  480,832   0.30
  A breaker          +296,006  209,084   1.42             +333,000  180,995   1.84
  B half on high VIX +551,638  184,977   2.98              +43,713  389,982   0.11
A kept 1,809 trades on paper. It transformed the held-out year - it sat out the
long Bank Nifty-led slide - but in the first two years it paused into drawdowns
that then recovered without it, making both profit and drawdown worse. B cut
profit more than drawdown in the held-out year and raised the drawdown in the
first two. Neither passes in both periods; neither is used.
For comparison, on the same pass mark, Bank Nifty watch-only (Nifty + Sensex):
  first two years +609,969 DD 121,890 (5.00) | held-out +280,335 DD 223,202 (1.26)
- lower drawdown and higher profit per unit of drawdown in both periods.
"""
import bisect
import json
import math
import sys

import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import indicators as ind
import pro_study as ps
import regime_study as rs
import skills_study as ss

INDICES = rs.INDICES
STOP, RESUME = 100_000.0, 50_000.0


def breaker_weights(trades, stop=STOP, resume=RESUME):
    """1 for a trade taken for real, 0 for one kept on paper. trades need when,
    exit_time and net, and are judged in entry order against the shadow curve of
    trades closed by then."""
    order = sorted(range(len(trades)), key=lambda k: trades[k]["when"])
    closes = sorted(range(len(trades)), key=lambda k: trades[k]["exit_time"])
    close_times = [trades[k]["exit_time"] for k in closes]
    weights = [1.0] * len(trades)
    equity = peak = 0.0
    done = 0
    paused = False
    for k in order:
        t = trades[k]["when"]
        upto = bisect.bisect_right(close_times, t)
        while done < upto:
            equity += trades[closes[done]]["net"]
            peak = max(peak, equity)
            done += 1
        dd = peak - equity
        if not paused and dd >= stop:
            paused = True
        elif paused and dd <= resume:
            paused = False
        weights[k] = 0.0 if paused else 1.0
    return weights


def vix_weights(dates, vix, q=0.75, window=250):
    """0.5 when yesterday's VIX was above the q-quantile of the `window` sessions
    before it, 1.0 otherwise (and when there is not a year of history yet)."""
    s = pd.Series(vix).sort_index()
    prev = s.shift(1)
    thr = s.shift(2).rolling(window, min_periods=window).quantile(q)
    out = []
    for d in dates:
        v, h = prev.get(d, np.nan), thr.get(d, np.nan)
        out.append(0.5 if (v == v and h == h and v > h) else 1.0)
    return out


def collect():
    config.TREND_DAY_ROOM = True
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    rows = []
    for k in INDICES:
        df = hists[k]
        F = rs.features(df, vix, nrv)
        ready = F["or_ready"].to_numpy()
        c = df["Close"].to_numpy()
        rsi = ind.rsi(df["Close"], 14).to_numpy()
        A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": c,
             "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
             "days": set(df.index.date)}
        gate = (lambda i, r, ready=ready, c=c, rsi=rsi: bool(ready[i]) and ss.rr_ok(r)
                and not ss.divergence(c, rsi, i, ps._side(r)))
        res = bt.run(k, df, gate=gate)
        pos = {t: n for n, t in enumerate(df.index)}
        sig = F["sigma"].to_numpy()
        for tr in res["trades"]:
            i = pos[tr["when"]]
            s = sig[i]
            if not (s == s) or s <= 0:
                continue
            p = ps.price(k, df, A, i, tr, ps.simulate(A, i, tr), s)
            if p:
                rows.append(p)
        print(f"[{k}] {len(res['trades'])} trades", flush=True)
    return rows, vix


def st(xs):
    s = ps.stats([x for x in xs if x["net"] != 0 or x.get("taken", True)])
    if not s:
        return None
    return {"n": s["n"], "total": round(s["total"]), "pf": round(s["pf"], 2), "dd": round(s["dd"]),
            "profit_per_dd": round(s["total"] / s["dd"], 2) if s["dd"] else None}


def scoped(rows, weights):
    out = []
    for r, w in zip(rows, weights):
        if w > 0:
            out.append(dict(r, net=r["net"] * w))
    return out


def main():
    rows, vix = collect()
    variants = {"live": [1.0] * len(rows),
                "A circuit breaker": breaker_weights(rows),
                "B half size when VIX high": vix_weights([r["when"].date() for r in rows], vix)}
    out = {"results": {}, "paper_or_half": {}}
    for name, w in variants.items():
        taken = scoped(rows, w)
        out["paper_or_half"][name] = sum(1 for x in w if x < 1.0)
        out["results"][name] = {
            per: {"all": st([r for r in taken if f(r)]),
                  "Nifty+Sensex": st([r for r in taken if f(r) and r["index"] != "BANKNIFTY"])}
            for per, f in (("in_sample", lambda r: r["when"] < rs.SPLIT),
                           ("held_out", lambda r: r["when"] >= rs.SPLIT))}
    base = out["results"]["live"]
    out["verdict"] = {}
    for name in ("A circuit breaker", "B half size when VIX high"):
        r = out["results"][name]
        ok = all(r[p]["all"]["dd"] < base[p]["all"]["dd"]
                 and (r[p]["all"]["profit_per_dd"] or 0) > (base[p]["all"]["profit_per_dd"] or 0)
                 for p in ("in_sample", "held_out"))
        out["verdict"][name] = {"keep": ok}
    print(json.dumps(out, indent=1, default=str))
    if len(sys.argv) > 1:
        json.dump(out, open(sys.argv[1], "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
