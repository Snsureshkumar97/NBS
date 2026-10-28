#!/usr/bin/env python3
"""sr_study's levels on made-up candles: yesterday's high, low and floor pivots (never today's), the next level
beyond a price, round numbers, and the room rule. No network."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

import pandas as pd
import sr_study as sr

idx = pd.DatetimeIndex(["2026-10-26 09:15", "2026-10-26 15:15", "2026-10-27 09:15", "2026-10-27 15:15"],
                       tz="Asia/Kolkata")
df = pd.DataFrame({"High": [110.0, 120.0, 200.0, 210.0], "Low": [90.0, 100.0, 190.0, 195.0],
                   "Close": [100.0, 105.0, 199.0, 205.0]}, index=idx)
lv = sr.day_levels(df)
d1, d2 = pd.Timestamp("2026-10-26").date(), pd.Timestamp("2026-10-27").date()
P = (120 + 90 + 105) / 3
check("the first day has no yesterday", d1 not in lv)
check("the second day's levels are the first day's: high, low, pivot, R1, S1, R2, S2",
      lv[d2] == sorted({120.0, 90.0, P, 2 * P - 90, 2 * P - 120, P + 30, P - 30}), lv[d2])
check("never today's own high or low", 210.0 not in lv[d2] and 190.0 not in lv[d2])
check("the next level above / below", sr.next_level([90, 100, 120], 105, True) == 120
      and sr.next_level([90, 100, 120], 105, False) == 100 and sr.next_level([90], 105, True) is None)
check("round numbers around the price", sr.round_levels("NIFTY", 24537) == [24400, 24500, 24600, 24700]
      and sr.round_levels("BANKNIFTY", 54210) == [53500, 54000, 54500, 55000])
check("room: a call 25 below a level with 20 risk passes at 1x, fails at 1.5x (30 needed)",
      sr.room_ok("LEVELS", 1.0, "NIFTY", {d2: [125.0]}, d2, 100.0, 20.0, True)
      and not sr.room_ok("LEVELS", 1.5, "NIFTY", {d2: [125.0]}, d2, 100.0, 20.0, True))
check("room: a put reads the level below", not sr.room_ok("LEVELS", 1.0, "NIFTY", {d2: [90.0]}, d2, 100.0, 20.0, False)
      and sr.room_ok("LEVELS", 1.0, "NIFTY", {d2: [70.0]}, d2, 100.0, 20.0, False))
check("no level that way, or no risk: passes", sr.room_ok("LEVELS", 1.0, "NIFTY", {d2: [50.0]}, d2, 100.0, 20.0, True)
      and sr.room_ok("LEVELS", 1.0, "NIFTY", {}, d2, 100.0, None, True))

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
