#!/usr/bin/env python3
"""
indian_strategies_robustness.py — do indian_strategies_study's winners hold up?
================================================================================
indian_strategies_study.py (22 Oct 2026) found five that earned more than the tool's own system in both periods - all
against the rules (Sensex: ORB15, ORB30, CAMARILLA, SUPERTR; Midcap: SUPERTR) - and CAMARILLA above the rules on Nifty
too. Eight strategies x four indices is ~30 comparisons, so some of that is luck. FIXED BEFORE THESE RESULTS: for each,
against the tool's rules on that index - robustness.py's walk-forward (half-years it beats the rules in; STEADY needs
60%+) and Monte Carlo 95% worst drop; and the strategy again with DOUBLE slippage (0.5%) against the rules as they are.
A strategy is worth building only if it is KEEP and STEADY, and still ahead with double slippage, in both periods.

RESULT (22 Oct 2026; indian_strategies_robustness_results.txt) - TWO HOLD UP, BOTH ON SENSEX:
  CAMARILLA on Sensex: +137,890 / +19,845 over the rules, beats them in 5 of 7 half-years, still +127,503 / +13,147 with
  double slippage; flagged only for a deeper Monte Carlo 95% worst drop (56,483 against the rules' 39,783 held-out).
  SUPERTR on Sensex: +132,272 / +10,087, 5 of 7 half-years, +123,883 / +5,901 with double slippage; the same flag.
  Not robust: ORB15 / ORB30 on Sensex (3 of 7 half-years; behind held-out at double slippage), SUPERTR on Midcap (4 of 7;
  +1,397 held-out at double slippage), CAMARILLA on Nifty (4 of 7 - and Nifty runs the TradingView rating, which earns
  more than either). Nothing built: a Camarilla or Supertrend system for Sensex is the user's call.

    /usr/bin/python3 indian_strategies_robustness.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indian_strategies_study as iss

CANDS = (("SENSEX", "ORB15"), ("SENSEX", "ORB30"), ("SENSEX", "CAMARILLA"), ("SENSEX", "SUPERTR"),
         ("MIDCPNIFTY", "SUPERTR"), ("NIFTY", "CAMARILLA"))


def _job(job):
    key, strat, slip = job
    import pro_study as ps
    real = iss.run

    def run_slip(k, s):
        rows = real(k, s)
        return rows
    if slip:
        # run() sets ps.SLIP = 0.0025 itself; wrap opt_net to price at 0.5% instead
        orig = iss.opt_net

        def opt_net(*a, **kw):
            ps.SLIP = 0.005
            return orig(*a, **kw)
        iss.opt_net = opt_net
    return job, run_slip(key, strat)


def main():
    import multiprocessing as mp
    import stop_day_study as sds
    jobs = [(k, s, slip) for k, s in CANDS for slip in (False, True)]
    tools = sorted({(k, "rules") for k, _ in CANDS})
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
        base = dict(pool.map(iss._tool, tools, chunksize=1))
    W = 170
    print("=" * W)
    print(" THE WINNERS, AGAINST THE TOOL'S RULES ON THEIR INDEX - per lot after costs, with robustness.py's walk-forward and"
          " Monte Carlo; then with double slippage")
    print("=" * W)
    for k, s in CANDS:
        b = base[(k, "rules")]
        rows = sorted(got[(k, s, False)], key=lambda r: r["when"])
        rows2 = sorted(got[(k, s, True)], key=lambda r: r["when"])
        print(f" {s} on {k}")
        print(sds.line("   the tool's rules", b))
        print(sds.line(f"   {s} (slippage 0.25%)", rows))
        print(sds.line(f"   {s} (slippage 0.50%)", rows2))
        print("   " + sds.verdict(f"{s} vs the rules", rows, b).strip().replace("\n", "\n   "))
        print("   " + sds.verdict(f"{s} at double slippage vs the rules", rows2, b).strip().split("\n")[0])


if __name__ == "__main__":
    main()
