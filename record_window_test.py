#!/usr/bin/env python3
"""The option recorder's strike window (the user, 28 Oct 2026: "widen the recorder and start collecting"): every strike
within 6% of the day's range is kept, so option selling (condors two expected moves out, 4-5% away) can be tested on
real prices - and nothing the narrow window kept is lost. No network."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

import record_options as ro

print("1. THE WINDOW")
for key, low, high, step in (("NIFTY", 22430.0, 22830.0, 50), ("BANKNIFTY", 53500.0, 54900.0, 100),
                             ("SENSEX", 72700.0, 74100.0, 100), ("MIDCPNIFTY", 13050.0, 13400.0, 25)):
    lo, hi = ro.strike_window(low, high, step)
    old_lo, old_hi = (low // step - ro.MARGIN_STEPS) * step, (high // step + 1 + ro.MARGIN_STEPS) * step
    check(f"{key}: reaches 6% beyond the day's range on both sides", lo <= low * (1 - ro.WIDE_PCT) and hi >= high * (1 + ro.WIDE_PCT),
          (lo, hi))
    check(f"{key}: keeps everything the narrow window kept", lo <= old_lo and hi >= old_hi)
    check(f"{key}: on the strike grid, and no more than one strike past 6%",
          lo % step == 0 and hi % step == 0 and lo > low * (1 - ro.WIDE_PCT) - step and hi < high * (1 + ro.WIDE_PCT) + step)
check("WIDE_PCT is 6%", ro.WIDE_PCT == 0.06)

print("2. THE RECORDER USES IT")
src = open(os.path.join(HERE, "record_options.py")).read()
check("record() takes its strikes from strike_window, on the day's own low and high",
      'lo, hi = strike_window(spot["low"].min(), spot["high"].max(), meta["strike_step"])' in src
      and 'lo <= i["strike"] <= hi' in src)
check("still read-only: it asks Zerodha for prices and places nothing",
      "place_order" not in src and "historical_data" in src)

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
