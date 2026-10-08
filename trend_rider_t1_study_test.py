#!/usr/bin/env python3
"""trend_rider_t1_study.py's exit walk on made-up candles: the stop before the target inside a candle, a T1 touch
moving the stop only from the next candle (to T1, or to entry for the breakeven variant), the Supertrend trail only
ever tightening it, and the 15:15 square-off."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trend_rider_t1_study as tr

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

def bars(o, h, l, c):
    n = len(c)
    return (np.array(o, float), np.array(h, float), np.array(l, float), np.array(c, float), np.array(["d"] * n))
none = lambda j: np.nan
cut = lambda n, at=None: np.array([at is not None and j >= at for j in range(n)])
RR = tr.RR                                   # 2.75: entry 100, risk 10 -> target 127.5, T1 at 1R = 110

# a call: candle 0 touches T1 (110), candle 1 dips to 105 - below T1, above the old stop 90: out at T1
b = bars([100, 111], [111, 112], [99, 105], [109, 106])
px, j, via = tr.walk(b, 0, "CE", 100.0, 90.0, 10.0, 1.0, "t1", False, none, cut(2))
check("T1 touched: the stop moves to T1 and the next candle's dip below it closes the trade at T1", (px, j, via) == (110.0, 1, "stop"), (px, j, via))
px, j, via = tr.walk(b, 0, "CE", 100.0, 90.0, 10.0, None, None, False, none, cut(2, 1))
check("without the T1 step-up: the same dip is held to the close", (px, j, via) == (106.0, 1, "square-off"), (px, j, via))
# the touch moves the stop only from the NEXT candle: a candle reaching T1 and falling back inside it is not stopped at T1
b2 = bars([100], [112], [104], [105])
px, j, via = tr.walk(b2, 0, "CE", 100.0, 90.0, 10.0, 1.0, "t1", False, none, cut(1, 0))
check("...from the next candle only", (px, j, via) == (105.0, 0, "square-off"), (px, j, via))
# breakeven: the stop to entry
b3 = bars([100, 108], [111, 109], [99, 99.5], [109, 100])
px, j, via = tr.walk(b3, 0, "CE", 100.0, 90.0, 10.0, 1.0, "be", False, none, cut(2))
check("breakeven at 1R: out at entry on the dip", (px, j, via) == (100.0, 1, "stop"), (px, j, via))
# the trail only tightens: a Supertrend below T1 changes nothing, one above it lifts the stop
b4 = bars([100, 112, 116], [111, 118, 117], [99, 111, 113.5], [110, 117, 114])
px, j, via = tr.walk(b4, 0, "CE", 100.0, 90.0, 10.0, 1.0, "t1", True, lambda j: 105.0 if j == 0 else 114.0, cut(3))
check("the Supertrend trail lifts the stop to 114 once tighter, and out there", (px, j, via) == (114.0, 2, "stop"), (px, j, via))
# the stop before the target inside one candle; the target alone; a gap through the stop filled at the open
b5 = bars([100], [130], [85], [120])
check("a candle through both: the stop", tr.walk(b5, 0, "CE", 100.0, 90.0, 10.0, None, None, False, none, cut(1))[2] == "stop")
b6 = bars([100], [130], [95], [120])
check("the target alone: at 127.5", tr.walk(b6, 0, "CE", 100.0, 90.0, 10.0, None, None, False, none, cut(1))[:2] == (127.5, 0))
b7 = bars([85], [88], [80], [86])
check("a gap through the stop: filled at the open", tr.walk(b7, 0, "CE", 100.0, 90.0, 10.0, None, None, False, none, cut(1))[0] == 85.0)
# a put mirrored: T1 at 90, the stop moves down to it
b8 = bars([100, 89], [101, 95], [89, 88], [91, 94])
px, j, via = tr.walk(b8, 0, "PE", 100.0, 110.0, 10.0, 1.0, "t1", False, none, cut(2))
check("a put: T1 at 90 moves the stop down to it, the bounce closes there", (px, j, via) == (90.0, 1, "stop"), (px, j, via))

print()
print("TREND RIDER T1 STUDY TEST PASSED" if not fails else f"TREND RIDER T1 STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
