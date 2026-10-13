#!/usr/bin/env python3
"""kronos_day_study.py's exits, traced by hand on made-up candles - before any of its results is trusted."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kronos_day_study as kds

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

# candles (high, low, close)
day = [(100.5, 99.0, 100.0), (101.0, 99.5, 100.8), (102.5, 100.5, 102.0), (102.2, 101.0, 101.5)]
print("1. ON THE INDEX")
check("a call's target reached on candle 2", kds.walk_index(day, "CE", 100.0, target=102.0) == (102.0, 2, "target"))
check("its stop first when a candle touches both", kds.walk_index(day, "CE", 100.0, target=100.9, stop=99.2)
      == (99.2, 0, "stop"))
check("neither: out at the last close", kds.walk_index(day, "CE", 100.0, target=110.0, stop=90.0) == (101.5, 3, "close"))
check("no levels at all: held to the close", kds.walk_index(day, "CE", 100.0) == (101.5, 3, "close"))
check("a put: stopped when the index rises to its stop", kds.walk_index(day, "PE", 100.0, target=95.0, stop=102.4)
      == (102.4, 2, "stop"))
check("halfway reached on candle 1 -> the stop moves to the entry, hit on candle... never (lows stay above 100)",
      kds.walk_index(day, "CE", 100.0, target=110.0, stop=95.0, be_at=100.9) == (101.5, 3, "close"))
dip = [(100.5, 99.8, 100.0), (101.2, 100.2, 101.0), (101.0, 99.9, 100.1), (100.5, 99.0, 99.2)]
check("...and when the index comes back below the entry, out at the entry",
      kds.walk_index(dip, "CE", 100.0, target=110.0, stop=95.0, be_at=101.0) == (100.0, 2, "stop"))
check("the stop does not move before halfway is reached (the same candle counts after the stop check)",
      kds.walk_index([(101.0, 99.0, 100.5)], "CE", 100.0, target=110.0, stop=98.0, be_at=101.0) == (100.5, 0, "close"))

print("1b. THE LADDER (the user: \"add a ladder like we have it in our tool\")")
check("T1 then T2 touched: the stop climbs to T1, and the pull-back to it closes the trade there",
      kds.walk_ladder(day, "CE", 100.0, 101.0, 102.0, 103.0) == (101.0, 3, "stop"))
check("T3 touched: out at T3", kds.walk_ladder(day, "CE", 100.0, 101.0, 102.0, 102.4) == (102.4, 2, "target"))
check("a stop below, touched first", kds.walk_ladder(day, "CE", 100.0, 101.0, 102.0, 103.0, stop=99.2) == (99.2, 0, "stop"))
check("T1 only: the stop at the entry, hit on the next candle's low", kds.walk_ladder(day, "CE", 100.0, 100.4, 100.6, 103.0)
      == (100.0, 1, "stop"))

print("2. ON THE OPTION'S OWN PRICE")
prem = lambda x, j: (x - 95.0)            # a call worth its distance above 95 - premium 5 at 100
check("+30% (6.5) reached on candle 2 (its high 102.5 -> 7.5); the -25% stop (3.75) never",
      kds.walk_premium(day, prem, 5.0, 0.30, 0.25) == (6.5, 2, "target"))
check("-10% (4.5): the candle's worst point first (its low 99.0 -> 4.0)", kds.walk_premium(day, prem, 5.0, 0.30, 0.10)[2] == "stop"
      and kds.walk_premium(day, prem, 5.0, 0.30, 0.10)[:2] == (4.5, 0))
check("neither: the last close's premium", kds.walk_premium(day, prem, 5.0, 2.0, 0.9) == (6.5, 3, "close"))
put = lambda x, j: (105.0 - x)            # a put worth its distance below 105
check("a put's best point is the candle's LOW (candle 3's 99.0 -> 6.0)",
      kds.walk_premium(dip, put, 5.0, 0.10, 0.5) == (5.5, 3, "target"))

print("3. THE TAB'S OWN LEVELS")
k = kds.kronos_levels(100.0, [100.5, 101.0, 102.0], [100.8, 101.5, 102.3], [99.6, 100.4, 101.2])
check("up: a call, target its last close, stop its lowest low", k == {"side": "CE", "entry": 100.0, "target": 102.0,
                                                                       "stop": 99.6}, k)
k = kds.kronos_levels(100.0, [100.5, 101.0, 102.0], [100.8, 101.5, 102.3], [100.2, 100.4, 101.2])
check("a path never against it: the stop is the target's distance the other way", k["stop"] == 98.0, k)
check("flat: no trade", kds.kronos_levels(100.0, [100.0], [100.5], [99.5]) is None)

print("4. COSTS")
flat = kds.net_rupees("NIFTY", 100.0, 100.0, 65)
check("bought and sold at the same price loses slippage and charges, a few hundred rupees a lot",
      -400 < flat < -50, round(flat, 2))
check("a 10-rupee rise in the premium is worth about 650 a lot, less those", 200 < kds.net_rupees("NIFTY", 100.0, 110.0, 65) < 650)

print()
print("KRONOS DAY STUDY TEST PASSED" if not fails else f"KRONOS DAY STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
