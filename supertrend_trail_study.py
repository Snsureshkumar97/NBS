#!/usr/bin/env python3
"""
supertrend_trail_study.py — does a continuous trail cut drawdown vs the REAL live exit?
================================================================================
The user, 2 Oct 2026, after the GitHub strategy comparison
(nbs-github-strategy-comparison.md) found its own low drawdown came partly
from a continuous Supertrend trail after breakeven: "add a continuous trail
after breakeven ... which we can use in my strategy to improve my drawdown."

A REAL GAP FOUND WHILE BUILDING THIS, WORTH BEING PLAIN ABOUT
    pro_study.simulate()'s plain mode - the "live" baseline EVERY study in
    this project has ever measured against - has never actually modelled
    today's real exit. tickets.py's _check_price(), read directly (not
    assumed): the moment T1 is touched, the stop ratchets to T1's OWN PRICE,
    unconditionally, no setting controls it. simulate()'s plain mode leaves
    the stop completely flat at the original level the whole trade. This
    does not undo any earlier KEEP/DROP verdict in this project (each
    compared against the SAME baseline, so the comparisons stayed fair
    relative to each other) - but testing THIS question specifically needs
    the baseline to actually be what's really live, or the answer would be
    about a strategy nobody runs.

    pro_study.simulate() gained two new parameters for this:
    ratchet_to_t1 (the real live behaviour, finally modelled) and
    trail_after_t1_supertrend (the new idea). Both default off/None -
    every existing caller, every earlier study, is completely unaffected.

THREE ROWS, NOT TWO
    1. "old 'live' baseline" - what every prior study called live (flat
       stop) - kept for transparency, not as the real comparison point.
    2. "TRUE live (T1 ratchets)" - the actual, corrected reproduction of
       today's real exit. THIS is the real baseline the trail should be
       measured against.
    3. "TRUE live + Supertrend trail" - row 2, plus the new idea.

    Supertrend(10, 2.5) for NIFTY and SENSEX, Supertrend(10, 3.0) for
    BANKNIFTY - NIFTY/BANKNIFTY reuse the exact parameters verified against
    the GitHub strategy's own published config
    (nbs-github-strategy-comparison.md); SENSEX has no external reference
    at all (that repo never covered it either) - its multiplier is a
    judgement call, matching NIFTY's rather than inventing a third number,
    flagged as exactly that.

    python3 supertrend_trail_study.py
"""
import math

import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import indicators as ind
import pro_study as ps
import regime_study as rs

INDICES = rs.INDICES
SPLIT = rs.SPLIT
ST_PARAMS = {"NIFTY": (10, 2.5), "BANKNIFTY": (10, 3.0), "SENSEX": (10, 2.5)}


def main():
    print("Loading history...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    base_gate = {k: rs.make_gates(F[k])["opening-range break"] for k in INDICES}

    rows = {"old": [], "true_live": [], "true_live_trail": []}
    for k in INDICES:
        df = hists[k]
        A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(),
             "cl": df["Close"].to_numpy(),
             "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
             "days": set(df.index.date)}
        length, mult = ST_PARAMS[k]
        st_line, _ = ind.supertrend(df, length, mult)
        st_arr = st_line.to_numpy()

        res = bt.run(k, df, gate=base_gate[k])
        pos = {t: n for n, t in enumerate(df.index)}
        sig = F[k]["sigma"].to_numpy()
        for tr in res["trades"]:
            i = pos[tr["when"]]
            s = sig[i]
            if not (s == s) or s <= 0:
                continue
            for name, kw in (("old", {}),
                              ("true_live", {"ratchet_to_t1": True}),
                              ("true_live_trail", {"ratchet_to_t1": True,
                                                    "trail_after_t1_supertrend": st_arr})):
                legs = ps.simulate(A, i, tr, **kw)
                p = ps.price(k, df, A, i, tr, legs, s)
                if p:
                    rows[name].append(p)

    def stats_of(lst):
        return {"is": ps.stats([r for r in lst if r["when"] < SPLIT]),
                "oos": ps.stats([r for r in lst if r["when"] >= SPLIT])}

    def line(name, r):
        a, b = r["is"], r["oos"]
        return (f" {name:30s} {a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    old, tl, tlt = stats_of(rows["old"]), stats_of(rows["true_live"]), stats_of(rows["true_live_trail"])
    print("=" * 128)
    print(" NIFTY + Bank Nifty + Sensex, real expiries, after costs")
    print("=" * 128)
    hdr = (f" {'exit policy':30s} {'IN-SAMPLE: trades, total, profit factor, worst drawdown':50s}"
           f"  | OUT-OF-SAMPLE (held-out final year)")
    print(hdr); print("-" * 128)
    print(line("old 'live' baseline (flat stop)", old))
    print(line("TRUE live (T1 ratchets - real)", tl))
    print(line("TRUE live + Supertrend trail", tlt))

    print("\n VERDICT 1 — what the T1-ratchet correction alone is actually worth "
          "(old baseline vs TRUE live)")
    di = tl["is"]["total"] - old["is"]["total"]; do = tl["oos"]["total"] - old["oos"]["total"]
    dd = tl["oos"]["dd"] - old["oos"]["dd"]
    print(f"   in-sample {di:>+10,.0f}   out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")

    print("\n VERDICT 2 — THE REAL QUESTION: does the trail beat TRUE live, both periods?")
    di = tlt["is"]["total"] - tl["is"]["total"]; do = tlt["oos"]["total"] - tl["oos"]["total"]
    dd = tlt["oos"]["dd"] - tl["oos"]["dd"]
    keep = di > 0 and do > 0
    print(f"   {'KEEP ' if keep else 'drop '} Supertrend trail   in-sample {di:>+10,.0f}"
          f"   out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")
    print(f"   held-out drawdown: TRUE live {tl['oos']['dd']:,.0f} -> with trail {tlt['oos']['dd']:,.0f} "
          f"({'improved' if tlt['oos']['dd'] < tl['oos']['dd'] else 'worse'} by "
          f"{abs(tlt['oos']['dd'] - tl['oos']['dd']):,.0f})")
    return {"old": old, "true_live": tl, "true_live_trail": tlt}


if __name__ == "__main__":
    main()
