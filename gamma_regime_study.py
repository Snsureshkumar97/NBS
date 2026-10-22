#!/usr/bin/env python3
"""
gamma_regime_study.py — do the tool's trades reverse less when option gamma is high (near expiry)?
================================================================================
The user, 22 Oct 2026, after reversal_study.py found nothing at entry that marks a reversal: "go into everything deep
search ... think you are a senior researcher and you have to solve this problem". The strongest published mechanism for
continuation against reversal intraday is option hedging: dealers short gamma hedge WITH the move (momentum), dealers
long gamma AGAINST it (reversal) - Baltussen, Da, Lammers & Martens, "Hedging demand and market intraday momentum",
JFE 2021 (60+ futures, 1974-2020); Barbon & Buraschi, "Gamma Fragility" (negative dealer gamma: momentum; positive:
reversal). India is untested; vendors' Nifty GEX borrows the US sign convention and says NSE data cannot confirm it.
In India the end users are net option BUYERS (retail), so the writers - props, market makers - are the ones short
gamma; an at-the-money option's gamma grows sharply as expiry nears, so the hedging flow, and with it continuation,
should be strongest close to expiry. Old option chains are not kept, so over three years the gamma proxy is TIME.

FIXED BEFORE ANY RESULT WAS SEEN (22 Oct 2026). Every trade, each index on its system (the rules on Nifty, Sensex and
Midcap; the TradingView rating on Nifty and Bank Nifty), by the trading days from entry to its contract's expiry (0 =
expiry day; real expiry calendars, Midcap monthly) and by G = 1 / sqrt(years to expiry) - an at-the-money option's
gamma, up to the volatility. Per bucket: trades, won, full stop, average and total per lot after costs, first two years
and held-out side by side, the rules and the rating separately.
  GAMMA MATTERS only if, for BOTH systems in BOTH periods, the low-gamma end (5+ trading days to expiry; G's lowest
  fifth) earns less a trade than the high-gamma end (expiry day; G's highest fifth). If it does, the rules that follow
  are tested inside the backtest as a separate, later step.

RESULT (22 Oct 2026; gamma_regime_study_results.txt) - NO. 5,289 trades: low-gamma trades (5+ trading days to expiry, G's
lowest fifth) earned less a trade than high-gamma ones only in half the system-period cells (rules held-out, the rating
in-sample) and more in the other half; the full-stop rate hardly moves with expiry (rules ~15-25%, the rating ~50-60%
in every bucket). The 5+ days end is mostly Midcap and Bank Nifty (monthly contracts), so index mixes with time.

    /usr/bin/python3 gamma_regime_study.py
"""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reversal_study as rv

DTE_BUCKETS = ((0, 0, "expiry day"), (1, 1, "1 day before"), (2, 2, "2 days"), (3, 4, "3-4 days"), (5, 99, "5+ days"))


def expiry_calc(key):
    """when -> (trading days to expiry, years to expiry) for this index's own contracts."""
    import backtest_intraday as bt
    import index_candidates_study as ics
    import pro_study as ps
    df = bt.fetch_history(key, years=3, use_cache=True)
    days = sorted(set(df.index.date))
    tset = set(days)
    pos = {d: n for n, d in enumerate(days)}
    real = ps._scheduled
    if key == "MIDCPNIFTY":
        ps._scheduled = lambda k, d, real=real: ics._monthly(d) if k == key else real(k, d)

    def f(when):
        d = pd.Timestamp(when).date()
        try:
            exp = ps.expiry_on_or_after(key, d, tset)
        except Exception:
            return None, None
        if exp is None or d not in pos:
            return None, None
        n = sum(1 for x in days[pos[d]:pos[d] + 40] if x <= exp) - 1
        return n, ps.years_to(exp, pd.Timestamp(when))

    def restore():
        ps._scheduled = real
    return f, restore


def main():
    import multiprocessing as mp
    import order_block_study as ob
    import stop_day_study as sds
    W = 160
    with mp.get_context("spawn").Pool(processes=5, maxtasksperchild=1) as pool:
        base = rv.collect(pool)
    for k, figs in ob.B_FIGS.items():
        s = sds.split(base[("rules", k)])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == figs, f"{k} rules must be room_fix_study's B {figs}"
    rows = []
    for (system, key), rr in base.items():
        f, restore = expiry_calc(key)
        for r in rr:
            n, yrs = f(r["when"])
            if n is None or yrs is None or yrs <= 0:
                continue
            rows.append({"system": system, "index": key, "net": r["net"], "is": r["when"] < sds.SPLIT,
                         "full_stop": r.get("closed_via") == "stop", "dte": n, "G": 1 / math.sqrt(yrs),
                         "when": r["when"], "side": r.get("side")})
        restore()
    A = pd.DataFrame(rows)
    print("=" * W)
    print(f" GAMMA AND REVERSALS - {len(A)} trades, 3 years, per lot after costs, ₹. Per bucket: trades, won, full stop, "
          "average a trade, total  (first two years | held-out)")
    print("=" * W)

    def cell(g):
        return (f"{len(g):>4} won {(g.net > 0).mean() if len(g) else 0:4.0%} stop {g.full_stop.mean() if len(g) else 0:4.0%}"
                f" avg ₹{g.net.mean() if len(g) else 0:>+6,.0f} tot ₹{g.net.sum():>+9,.0f}")
    verdict = {}
    for system in ("rules", "tv"):
        S = A[A.system == system]
        print(f" {('THE RULES (Nifty, Sensex, Midcap)' if system == 'rules' else 'THE TRADINGVIEW RATING (Nifty, Bank Nifty)')}")
        print("   trading days to expiry")
        for lo, hi, name in DTE_BUCKETS:
            g = S[(S.dte >= lo) & (S.dte <= hi)]
            print(f"     {name:14s} {cell(g[g['is']])}  |  {cell(g[~g['is']])}")
        cuts = list(np.quantile(S[S["is"]].G, [0.2, 0.4, 0.6, 0.8]))
        print(f"   G (gamma up to volatility), fifths cut at {', '.join(f'{c:.1f}' for c in cuts)}")
        b = S.G.apply(lambda v: int(np.searchsorted(cuts, v, side='right')))
        for q in range(5):
            g = S[b == q]
            print(f"     Q{q + 1} {'(lowest)' if q == 0 else '(highest)' if q == 4 else '':10s} {cell(g[g['is']])}  |  {cell(g[~g['is']])}")
        for key in sorted(S["index"].unique()):
            g = S[S["index"] == key]
            far, exp = g[g.dte >= 5], g[g.dte == 0]
            print(f"     {key:11s} expiry day {cell(exp[exp['is']])}  |  {cell(exp[~exp['is']])}")
            print(f"     {'':11s} 5+ days    {cell(far[far['is']])}  |  {cell(far[~far['is']])}")
        for p in ("is", "oos"):
            P = S[S["is"]] if p == "is" else S[~S["is"]]
            bp = b[P.index]
            far, exp = P[P.dte >= 5], P[P.dte == 0]
            low, high = P[bp == 0], P[bp == 4]
            verdict[(system, p)] = bool((far.net.mean() < exp.net.mean()) and (low.net.mean() < high.net.mean()))
    ok = all(verdict[(s, p)] for s in ("rules", "tv") for p in ("is", "oos"))
    print(f"\n GAMMA MATTERS? {'YES' if ok else 'NO'} - low-gamma trades earned less a trade than high-gamma ones: "
          + ", ".join(f"{s} {p}: {'yes' if verdict[(s, p)] else 'no'}" for s in ("rules", "tv") for p in ("is", "oos")))
    A.to_pickle(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gamma_regime_rows.pkl"))
    return A


if __name__ == "__main__":
    main()
