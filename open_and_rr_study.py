#!/usr/bin/env python3
"""
open_and_rr_study.py — drop the 09:15-09:45 wait, and 0.7 reward for 1 risk?
============================================================================
PRE-DECLARED 16 Sep 2026, before this was run, at the user's request: they want
more trades, by letting the tool trade from 09:15 instead of waiting out the
first half hour, and by asking T3 to be only 0.7x the stop instead of 1x.

Four variants, each on the rules live today (no break needed, no entry into an
RSI divergence, trend-day room, all three indices, exit at T2), priced as
pro_study.py prices them: real expiries, per lot, Zerodha's charges, 0.25%
slippage, drawdowns walked in time order from zero.

  live       wait for 09:15-09:45, T3 >= 1.0x the stop   (what runs today)
  A          no wait, T3 >= 1.0x
  B          wait, T3 >= 0.7x
  C          no wait, T3 >= 0.7x                          (both changes)

A change is adopted only if it makes MORE money than the live rules in BOTH the
first two years and the held-out year. The worst drawdown and profit per unit of
drawdown are reported beside it, because more trades usually means a deeper
drawdown - the user decides whether a given trade-off is worth it. One setting
each, not swept, not re-tuned after the result.

    python3 open_and_rr_study.py [out.json]

RESULT, history to 11 Sep 2026, per lot after costs, all three indices:
                      first two years                     held-out year
                      n     profit   DD       p/DD        n     profit   DD       p/DD
  live                3638  629,112  174,098  3.61        2520  144,028  480,832  0.30
  A no wait, 1.0x     3726  573,879  195,985  2.93        2570  207,933  426,028  0.49
  B wait, 0.7x        3912  704,248  198,059  3.56        2650   61,958  531,939  0.12
  C no wait, 0.7x     3995  602,928  225,744  2.67        2699  148,473  457,112  0.32
None passes: each is worse than live in one of the two periods. Notes.
  A loses 55,233 in the first two years and gains 63,905 in the held-out year
    (drawdown 480,832 -> 426,028). It also barely buys what it was meant to buy:
    138 more trades out of 6,158, about 2%, because the 4-bar gap and the daily
    cap refill the day anyway - dropping the wait mostly moves entries earlier.
  B is the 1x rule loosened: 404 more trades, +75,136 in the first two years,
    -82,070 in the held-out year with the deepest drawdown of the four
    (531,939) - the same shape seen on 15 Sep when the rule was tested at 0.0.
    Bank Nifty held-out falls to -170,217.
  C is both: 536 more trades and roughly live's money (-26,184 | +4,445) for a
    29% deeper first-two-years drawdown.
Neither change adopted; live rules unchanged.
"""
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

# name -> (wait for the opening range, reward:risk multiple on T3)
VARIANTS = {"live": (True, 1.0),
            "A no wait, 1.0x": (False, 1.0),
            "B wait, 0.7x": (True, 0.7),
            "C no wait, 0.7x": (False, 0.7)}


def rr_ok(r, mult):
    """skills_study.rr_ok with the multiple exposed: T3 at least mult x the stop."""
    return bool(r.get("risk_points") and r["index_targets"][2] is not None
                and abs(r["index_targets"][2] - r["spot"]) >= mult * r["risk_points"])


def run(hists, vix, nrv, wait, mult):
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
        gate = (lambda i, r, ready=ready, c=c, rsi=rsi:
                (bool(ready[i]) or not wait) and rr_ok(r, mult)
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
        print(f"[wait={wait} rr={mult}] {k}: {len(res['trades'])} trades", flush=True)
    return rows


def st(xs):
    s = ps.stats(xs)
    if not s:
        return None
    return {"n": s["n"], "total": round(s["total"]), "avg": round(s["avg"]), "pf": round(s["pf"], 2),
            "dd": round(s["dd"]), "win": round(s["win"], 1),
            "profit_per_dd": round(s["total"] / s["dd"], 2) if s["dd"] else None}


def summary(rows):
    out = {}
    for per, f in (("in_sample", lambda r: r["when"] < rs.SPLIT), ("held_out", lambda r: r["when"] >= rs.SPLIT)):
        part = [r for r in rows if f(r)]
        out[per] = {"all": st(part), **{k: st([r for r in part if r["index"] == k]) for k in INDICES},
                    "Nifty+Sensex": st([r for r in part if r["index"] != "BANKNIFTY"])}
    return out


def main():
    was = getattr(config, "TREND_DAY_ROOM", False)
    config.TREND_DAY_ROOM = True
    try:
        hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
        vix = rs.load_vix()
        nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
        out = {"results": {name: summary(run(hists, vix, nrv, wait, mult))
                           for name, (wait, mult) in VARIANTS.items()}}
    finally:
        config.TREND_DAY_ROOM = was
    base = out["results"]["live"]
    out["verdict"] = {}
    for name in VARIANTS:
        if name == "live":
            continue
        r = out["results"][name]
        out["verdict"][name] = {
            "in_sample_vs_live": r["in_sample"]["all"]["total"] - base["in_sample"]["all"]["total"],
            "held_out_vs_live": r["held_out"]["all"]["total"] - base["held_out"]["all"]["total"],
            "extra_trades": (r["in_sample"]["all"]["n"] + r["held_out"]["all"]["n"]
                             - base["in_sample"]["all"]["n"] - base["held_out"]["all"]["n"]),
            "keep": (r["in_sample"]["all"]["total"] > base["in_sample"]["all"]["total"]
                     and r["held_out"]["all"]["total"] > base["held_out"]["all"]["total"])}
    print(json.dumps(out, indent=1))
    if len(sys.argv) > 1:
        json.dump(out, open(sys.argv[1], "w"), indent=1)


if __name__ == "__main__":
    main()
