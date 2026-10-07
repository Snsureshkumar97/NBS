#!/usr/bin/env python3
"""scalp10_1to1_study.py's 5-minute exit walk on made-up candles: a stop and a target in different candles, a candle
that reaches both (the stop when counting cautiously, the target when hopeful, and in the middle reading whichever
the candle's shape puts first), the day's close, and the 15:15 cut-off the Trend Rider keeps."""
import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scalp10_1to1_study as s11

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

def M(o, h, l, c, last=None, tod=None):
    n = len(c)
    return {"o": np.array(o, float), "h": np.array(h, float), "l": np.array(l, float), "c": np.array(c, float),
            "day_last": np.array(last if last else [False] * (n - 1) + [True]),
            "tod": np.array(tod if tod else [dt.time(10, 0)] * n)}

stop, tgt = (lambda p: 90.0), (lambda p: 110.0)
cons, mid, opt, amb = s11.walk5(M([100, 101], [105, 111], [95, 99], [101, 110]), 0, "CE", stop, tgt)
check("a call: the target in the second candle", cons == mid == opt == (110.0, 1, "target") and not amb, cons)
cons, mid, opt, amb = s11.walk5(M([100, 101], [105, 103], [95, 89], [101, 92]), 0, "CE", stop, tgt)
check("a call: the stop in the second candle", cons == (90.0, 1, "stop") and not amb, cons)
up = M([100], [112], [88], [108])          # closed up: open, low, high, close - the low (a call's stop) came first
cons, mid, opt, amb = s11.walk5(up, 0, "CE", stop, tgt)
check("a both-way candle that closed up: a call's stop first in the middle reading",
      amb and cons == (90.0, 0, "stop") and opt == (110.0, 0, "target") and mid == cons, (cons, mid, opt))
cons, mid, opt, amb = s11.walk5(up, 0, "PE", lambda p: 110.0, lambda p: 90.0)
check("...and a put's target (the low) first", amb and mid == (90.0, 0, "target"), mid)
down = M([100], [112], [88], [92])         # closed down: open, high, low, close - the high came first
cons, mid, opt, amb = s11.walk5(down, 0, "CE", stop, tgt)
check("a both-way candle that closed down: a call's target (the high) first in the middle reading", mid == (110.0, 0, "target"), mid)
cons, mid, opt, amb = s11.walk5(M([100, 101, 102], [104, 105, 106], [96, 97, 98], [101, 102, 103]), 0, "CE", stop, tgt)
check("neither: out at the day's last close", cons == (103.0, 2, "close"), cons)
cut = M([100, 101, 102], [104, 105, 106], [96, 97, 98], [101, 102, 103], tod=[dt.time(15, 0), dt.time(15, 10), dt.time(15, 15)])
cons, _, _, _ = s11.walk5(cut, 0, "CE", stop, tgt, cutoff_tod=dt.time(15, 10))
check("the Trend Rider: out at the close of the candle that ends 15:15", cons == (102.0, 1, "close"), cons)

print()
print("SCALP10 1TO1 STUDY TEST PASSED" if not fails else f"SCALP10 1TO1 STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
