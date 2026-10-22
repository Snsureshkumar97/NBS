#!/usr/bin/env python3
"""
system_winrate_study.py — the rules against the TradingView rating on Nifty and Bank Nifty: how often each loses
================================================================================
The user, 22 Oct 2026: "how to avoid these kind of trades it happened multiple times the tool entering the trade after
that the market will get reversal and we hit the stop loss how to fix this and avoid this". Their own Indian trades
since 1 Oct: the rules won 61-67% on Nifty, Sensex and Midcap; the TradingView rating - Bank Nifty since 14 Oct,
Nifty since 21 Oct - won 2 of 11, 10 at the stop. Twenty-odd entry filters tested since September (confirmation
closes, RSI caps, range exhaustion, ADX 25, the hour's trend, CPR, order blocks, OI walls, who moved ...) found no way
to tell a reversing entry in advance without throwing the winners away with it; what does differ is the system.

Each system as live runs it, 3 years, per lot after costs: the rules (order_block_study.rules_rows) and the
TradingView rating with the tool's exit (tv_ratings_study tv_ours), on Nifty and on Bank Nifty. For each: trades, won,
how many ended at the full stop, the longest run of losing trades, the worst week, the total, profit factor and worst
drop - first two years and held-out. Information for the user's choice of system per index; nothing is changed.

RESULT (22 Oct 2026; system_winrate_study_results.txt) - THE REVERSALS ARE THE TRADINGVIEW RATING'S NATURE, AND ITS PRICE.
  Nifty    rules: won 52% / 50%, full stop 21% / 17% of trades, up to 9 / 8 losses in a row, +60,462 / +51,814, worst
           drop 31,442 / 27,315.  TradingView rating: won 39% / 39%, full stop 51% / 52%, up to 14 / 13 in a row,
           +267,670 / +143,011 (PF 1.46 / 1.51), worst drop 26,758 / 33,562.
  BankNifty rules: won 49% / 46%, full stop 20% / 16%, up to 9 / 10, +43,557 / +19,798, worst drop 42,025 / 40,536.
           TradingView rating: won 37% / 38%, full stop 55% / 54%, up to 16 / 10, +181,285 / +76,234, worst drop
           68,139 / 53,236.
Half the TradingView rating's trades end at the full stop by design - it earns from fewer, bigger winners - and still
makes three to four times the rules' profit on Nifty with about the same worst drop. The rules lose far less often and
earn far less. A choice of temperament, set per index in Trade settings; nothing changed.

    /usr/bin/python3 system_winrate_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
KEYS = ("NIFTY", "BANKNIFTY")


def _job(job):
    key, system = job
    if system == "rules":
        import order_block_study as ob
        return job, ob.rules_rows(key)
    import tv_ratings_study as tvs
    _, out = tvs._tv_job(key)
    return job, out["tv_ours"]


def describe(rows):
    import pandas as pd
    import stop_day_study as sds
    out = {}
    for p, sel in (("is", [r for r in rows if r["when"] < sds.SPLIT]), ("oos", [r for r in rows if r["when"] >= sds.SPLIT])):
        net = [r["net"] for r in sel]
        streak = best = 0
        for x in net:
            streak = streak + 1 if x <= 0 else 0
            best = max(best, streak)
        full_stop = sum(1 for r in sel if r.get("closed_via") == "stop")
        wk = pd.Series(net, index=[pd.Timestamp(r["when"]).to_period("W") for r in sel]).groupby(level=0).sum() if sel else pd.Series(dtype=float)
        s = sds.split(sel)["is" if p == "is" else "oos"]
        out[p] = {"n": len(net), "won": sum(1 for x in net if x > 0) / len(net) if net else 0,
                  "full_stop": full_stop / len(net) if net and any("closed_via" in r for r in sel) else None,
                  "streak": best, "worst_week": float(wk.min()) if len(wk) else 0, "total": sum(net),
                  "pf": s["pf"], "dd": s["dd"]}
    return out


def main():
    import multiprocessing as mp
    jobs = [(k, s) for k in KEYS for s in ("rules", "tv_rating")]
    with mp.get_context("spawn").Pool(processes=4, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    W = 150
    print("=" * W)
    print(" THE RULES AGAINST THE TRADINGVIEW RATING - each as live runs it, 3 years, per lot after costs, ₹")
    print("=" * W)
    f = lambda d: (f"{d['n']:>5} trades  won {d['won']:4.0%}  " + (f"full stop {d['full_stop']:4.0%}  " if d["full_stop"] is not None else "")
                   + f"losses in a row up to {d['streak']:>2}  worst week ₹{d['worst_week']:>+8,.0f}  total ₹{d['total']:>+9,.0f}"
                   f"  PF {d['pf']:.2f}  worst drop ₹{d['dd']:>7,.0f}")
    for k in KEYS:
        print(f" {k}")
        for s in ("rules", "tv_rating"):
            d = describe(got[(k, s)])
            print(f"   {s:10s} first two years {f(d['is'])}")
            print(f"   {'':10s} held-out        {f(d['oos'])}")
    return got


if __name__ == "__main__":
    main()
