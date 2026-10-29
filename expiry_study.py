#!/usr/bin/env python3
"""
expiry_study.py — buy a further expiry instead of the nearest (six years, all four indices, realistic option prices)
================================================================================
The user, 28 Oct 2026: "yes revert the rules exit and test further expiry". With option prices corrected to the
recorder's real ones (pricing.py) the weekly-option indices lost money on nearly every system - weekly options lose
their value fastest in their last days (pricing_calibration.py: real at-the-money volatility about twice the model's
on expiry day, 1.27x a day or two before) - while Midcap, on monthly options, made money on every system.

FIXED BEFORE ANY RESULT WAS SEEN (28 Oct 2026). Every trade the same, only the contract bought changes
(pro_study.trade_expiry: the first expiry with at least MIN_DTE calendar days to run; at the money as before):
  MIN_DTE 0    the nearest expiry - what the tool buys today
  MIN_DTE 7    THE CANDIDATE: at least a week to run
  MIN_DTE 3 / 14  either side, so one lucky setting cannot pass for a rule
on the systems the tool runs: the rules (as they ship, the stop steps), the TradingView rating, the Trend Rider
(Nifty / Bank Nifty), the big candle - and the COIN (the rules' gates and exit, a coin for the direction: it must
still lose). honest_retest.py's harness: stops filled honestly, per lot after costs, one ticket at a time per index.
BETTER only if MIN_DTE 7 makes more than MIN_DTE 0 in BOTH periods (to 15 Aug 2025, then held out), the four indices
pooled for each system, with robustness.py's walk-forward / Monte Carlo line. A further expiry costs more to buy (more
premium per lot); the per-lot figures do not show that.

RESULT (28 Oct 2026, history to 26 Oct 2026; expiry_study_results.txt). Per lot after costs, the four indices pooled,
to 15 Aug 2025 / held out - a further expiry is better at every step for every system, on every index (a smooth
pattern, not one lucky setting):
                   nearest (today)         at least 7 days (THE CANDIDATE)   at least 14 days
  the rules        -596,164 /  -65,453     -422,892 /  +64,726  KEEP        +586,810 / +343,813
  TradingView      -485,442 /  +62,487     -286,302 / +251,000  KEEP      +1,226,589 / +621,547
  Trend Rider      -230,303 /  -16,815     -172,636 /  +64,305  KEEP        +426,364 / +194,035
  the big candle   +174,715 / +107,401     +204,852 / +129,697  KEEP        +586,775 / +184,523
  the coin       -2,311,638 / -723,477   -2,025,050 / -597,282             -970,126 / -340,212 (still loses)
At least 7 days still leaves the weekly indices losing in the first period; at least 14 days makes every system
money on all four in both periods (TradingView: Nifty +313,370 / +138,704, Bank Nifty +371,593 / +150,414, Sensex
+278,374 / +80,863, Midcap +263,252 / +251,565). Not yet trusted: the correction's evidence for options 14+ days out
is thin (the recorder keeps the nearest two expiries only - a Nifty weekly's second is 7-13 days out), a further
expiry costs more premium a lot, and its spread may be wider than the 0.25% slippage assumed.

RE-RUN (29 Oct 2026, pricing.py fixed - 3ac57e4: the 14+ day row let a far option gain from the clock, real Bank Nifty
  far trades showed the model +296 a trade too rosy; now -31, se 95). At least 0 / 3 / 7 / 14 days, to 15 Aug 2025 /
  held out: the rating -518,931 / -51,257, -336,097 / +24,201, -484,495 / +2,035, -82,353 / +90,302; the rules
  -696,430 / -188,741 ... -489,743 / -130,935; the big candle +171,721 / +95,467 ... +265,224 / +115,178; the coin
  -2,266,670 / -828,319 ... -2,036,449 / -785,067 (it loses at every expiry - before the fix it lost half as much at
  14+ days). Further out still helps, but far less than the 28 Oct figures said, and no longer smoothly.

    /usr/bin/python3 expiry_study.py        (HOME at the six-year history)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
KEYS = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
MIN_DTES = (0, 3, 7, 14)
SYSTEMS = ("RULES", "TV", "BIG_CANDLE", "COIN")


def _job(job):
    key, system, m = job
    import honest_retest as h
    import pricing
    pricing.MIN_DTE = m
    if system == "RULES":
        return job, {"RULES": h._job((key, "exit", "TODAY"))[1]}
    if system == "COIN":
        return job, {"COIN": h._job((key, "entry", "COIN_A_ONLY"))[1]}
    if system == "BIG_CANDLE":
        return job, {"BIG_CANDLE": h._job((key, "bc", None))[1]}
    got = h._job((key, "tv", None))[1]
    return job, {"TV_RATING": got["TV_RATING"], **({"TREND_RIDER": got["TREND_RIDER"]} if got["TREND_RIDER"] else {})}


def main():
    import multiprocessing as mp
    import pandas as pd
    import stop_day_study as sds
    jobs = [(k, s, m) for m in MIN_DTES for s in SYSTEMS for k in KEYS]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    s = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    names = ("RULES", "TV_RATING", "TREND_RIDER", "BIG_CANDLE", "COIN")
    R = {}                                           # (name, index, m) -> rows
    for (k, _, m), out in got.items():
        for name, rows in out.items():
            R[(name, k, m)] = s(rows)
    for scope, keys in (("ALL FOUR POOLED", KEYS),) + tuple((k, (k,)) for k in KEYS):
        print("=" * 120)
        print(scope, "  per lot after costs, realistic option prices  (to 15 Aug 2025 | held out)")
        for name in names:
            rows_m = {m: s([r for k in keys for r in R.get((name, k, m), [])]) for m in MIN_DTES}
            if not rows_m[0]:
                continue
            print(f"  {name}")
            for m in MIN_DTES:
                print(sds.line(f"     at least {m:2d} days to expiry" + (" (today)" if m == 0 else ""), rows_m[m]))
            text = sds.verdict(f"{name}: at least 7 days vs the nearest", rows_m[7], rows_m[0]).strip()
            print("   " + (text.replace("\n", "\n   ") if scope == "ALL FOUR POOLED" else text.splitlines()[0]))


if __name__ == "__main__":
    main()
