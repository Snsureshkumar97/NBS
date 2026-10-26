#!/usr/bin/env python3
"""
big_candle_6y_study.py — the big candle (as built on paper) against the rules over SIX years
================================================================================
The user, 26 Oct 2026, after the 3-year comparison (big_candle_robustness.py): "can we do it for last 6 years". The same
footing and the same three systems - the tool's rules alone, the big candle alone ("K1.5 end 2R", big_candle.py), and
the rules with the big candle taken where they hold nothing - on 15-minute candles from 3 Aug 2020 to 26 Oct 2026.
The candles and daily India VIX come from Zerodha (the account's own session on the server), saved under a scratch
HOME as the 3-year cache names, so every harness reads six years unchanged; on the 3 years both have they are equal to
the cache to the paisa. Nothing was tuned on the first three years: the rule is the one fixed on 26 Oct.

Nifty and Bank Nifty had weekly options all six years. Sensex options were relaunched only in May 2023 - before that
its figures are what an at-the-money option WOULD have made, priced by the model (also shown from May 2023 alone).
Midcap Select options began in 2023: not run. Lot sizes are today's throughout (per lot).

RESULT (26 Oct 2026; big_candle_6y_study_results.txt) - ADDED TO THE RULES IT HOLDS OVER SIX YEARS TOO, INCLUDING THE
THREE IT WAS NEVER CHOSEN ON (Aug 2020 - Sep 2023). The rules + the big candle against the rules alone, per lot:
  Nifty +129,635 (Aug 2020 - Aug 2025) / +5,448 (since), better in 9 of 13 half-years (STEADY), 6 of 7 calendar years
  Bank Nifty +118,431 / +13,836, 12 of 13 half-years (STEADY), 6 of 7 years
  Sensex +116,299 / +22,908, 10 of 13 half-years (STEADY), 6 of 7 years (Sensex before May 2023 is model-priced only)
  In the earlier three years alone (the six-year in-sample less the three-year): Nifty about +15,000, Bank Nifty about
  +88,000, Sensex about +26,000. The losing years: Nifty 2022 (-10,595), Bank Nifty 2025 (-1,222), Sensex 2023
  (-31,988). Worst drop about the same in 2020-2025, deeper since (Nifty +10,363, Bank Nifty +11,104, Sensex +5,061).
  On its own the big candle beats the rules only on Bank Nifty.

    HOME=<scratch with the 6-year history> /usr/bin/python3 big_candle_6y_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

KEYS = ("NIFTY", "BANKNIFTY", "SENSEX")
V = ("K1.5 end 2R", 1.5, True, "end", "2R")


def _job(job):
    key, what = job
    if what == "rules":
        import order_block_study as ob
        return job, ob.rules_rows(key)
    import big_candle_study as bcs
    return job, bcs.run(key, V)


def main():
    import multiprocessing as mp
    import pandas as pd
    import big_candle_study as bcs
    import stop_day_study as sds
    jobs = [(k, w) for k in KEYS for w in ("rules", "bc")]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    W = 150
    print("=" * W)
    print(" SIX YEARS (Aug 2020 - Oct 2026): THE RULES, THE BIG CANDLE, AND THE TWO TOGETHER - per lot after costs, ₹")
    print("=" * W)
    print(f" {'':46s} {'AUG 2020 - 15 AUG 2025':66s}  | 15 AUG 2025 - 26 OCT 2026")
    years = {}
    for k in KEYS:
        rules = sorted(got[(k, "rules")], key=lambda r: pd.Timestamp(r["when"]))
        bc = sorted(got[(k, "bc")], key=lambda r: pd.Timestamp(r["when"]))
        both, added = bcs.with_tool(rules, bc)
        print(f" {k}")
        print(sds.line("   the rules alone", rules))
        print(sds.line("   the big candle alone", bc))
        print(sds.line(f"   the rules + the big candle (+{added})", both))
        print("   " + sds.verdict("rules + big candle vs the rules", both, rules).strip().replace("\n", "\n   "))
        print("   " + sds.verdict("big candle alone vs the rules", bc, rules).strip().replace("\n", "\n   "))
        if k == "SENSEX":
            since = lambda rows: [r for r in rows if pd.Timestamp(r["when"]).date() >= pd.Timestamp("2023-05-15").date()]
            print(sds.line("   from May 2023 (real Sensex options): rules", since(rules)))
            print(sds.line("   from May 2023: rules + big candle", since(both)))
        for name, rows in (("rules", rules), ("big candle", bc), ("rules + big candle", both)):
            for r in rows:
                y = pd.Timestamp(r["when"]).year
                years.setdefault((k, y), {}).setdefault(name, 0.0)
                years[(k, y)][name] += r["net"]
    print("\n BY CALENDAR YEAR (2020 from August, 2026 to 26 October)")
    print(f" {'':12s}{'year':>6s}{'rules':>12s}{'big candle':>12s}{'together':>12s}{'added':>10s}")
    for (k, y) in sorted(years):
        v = years[(k, y)]
        r, b, t = v.get("rules", 0.0), v.get("big candle", 0.0), v.get("rules + big candle", 0.0)
        print(f" {k:12s}{y:>6d}{r:>12,.0f}{b:>12,.0f}{t:>12,.0f}{t - r:>+10,.0f}")


if __name__ == "__main__":
    main()
