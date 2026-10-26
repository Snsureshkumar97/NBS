#!/usr/bin/env python3
"""
big_candle_robustness.py — the first big candle with its stop at the candle's other end: does it hold up?
================================================================================
big_candle_study.py's primary rule (stop at the candle's middle) lost to the tool everywhere; in its sensitivity grid
the stop at the candle's OTHER END did far better. Found by looking, so FIXED BEFORE THESE RESULTS: K1.5 and K2.0 with
that stop and a target of twice the risk, each on its own and ADDED to the tool's system (taken only where the tool
holds nothing, its own trades first), against the rules on every index and the TradingView rating on Nifty and Bank
Nifty - robustness.py's walk-forward (STEADY at 60%+ of 7 half-years) and Monte Carlo 95% worst drop, and again at
double slippage (0.5%).

RESULT (26 Oct 2026; big_candle_robustness_results.txt) - AS AN ADD-ON TO THE RULES IT HOLDS UP; ON ITS OWN, OR WITH
THE TRADINGVIEW RATING, IT DOES NOT. The rules + K1.5 when the rules are flat, against the rules alone, per lot:
  Nifty +114,652 / +5,462 (5 of 7 half-years, STEADY), Bank Nifty +30,084 / +13,833 (6 of 7, STEADY), Sensex +90,549 /
  +22,908 (6 of 7, STEADY), Midcap +18,059 / +9,219 (4 of 7, FRAGILE); K2.0 the same way on Nifty and Sensex (STEADY),
  fragile on Bank Nifty, a loss in-sample on Midcap. The cost: a deeper worst drop held-out (Nifty +10,363, Bank Nifty
  +8,180, Sensex +5,061). On its own K1.5 beats the rules only on Bank Nifty (+41,373 / +45,473, STEADY, still ahead at
  double slippage); added to the TradingView rating it loses on Nifty and is mixed on Bank Nifty. About 5-6 trades a
  month per index. Held-out gains are modest next to in-sample - a candidate found among ~14 variants, not yet built.

    /usr/bin/python3 big_candle_robustness.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import big_candle_study as bcs

CANDS = [("K1.5 end 2R", 1.5, True, "end", "2R"), ("K2.0 end 2R", 2.0, True, "end", "2R")]


def _job(job):
    k, v, slip = job
    return job, bcs.run(k, v, slip)


def main():
    import multiprocessing as mp
    import indian_strategies_study as iss
    import stop_day_study as sds
    jobs = [(k, v, s) for k in bcs.INDICES for v in CANDS for s in (0.0025, 0.005)]
    tools = [(k, s) for k in bcs.INDICES for s in ("rules", "tv") if s == "rules" or k in ("NIFTY", "BANKNIFTY")]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
        base = dict(pool.map(iss._tool, tools, chunksize=1))
    for k in bcs.INDICES:
        print("=" * 120)
        print(k)
        for v in CANDS:
            rows = sorted(got[(k, v, 0.0025)], key=lambda r: r["when"])
            rows2 = sorted(got[(k, v, 0.005)], key=lambda r: r["when"])
            print(sds.line(f"   {v[0]}", rows))
            print(sds.line(f"   {v[0]} at 0.5% slippage", rows2))
            for s in ("rules", "tv"):
                if (k, s) not in base:
                    continue
                print("   " + sds.verdict(f"{v[0]} vs {s}", rows, base[(k, s)]).strip().replace("\n", "\n   "))
                print("   " + sds.verdict(f"{v[0]} 0.5% slip vs {s}", rows2, base[(k, s)]).strip().split("\n")[0])
                both, added = bcs.with_tool(base[(k, s)], rows)
                print("   " + sds.verdict(f"{s} + {v[0]} when flat (+{added}) vs {s}", both, base[(k, s)]).strip()
                      .replace("\n", "\n   "))


if __name__ == "__main__":
    main()
