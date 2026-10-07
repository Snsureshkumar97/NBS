#!/usr/bin/env python3
"""stop_supertrend_study.simulate on hand-made candles: following the Supertrend line from entry tightens the stop
only with a line on the trade's side, a candle's line applies from the NEXT candle, and without the follow it is
exactly opening_window_study.simulate_live."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

import opening_window_study as ows
import stop_supertrend_study as sss

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

def arrays(hi, lo, cl):
    n = len(cl)
    return {"hi": np.array(hi, float), "lo": np.array(lo, float), "cl": np.array(cl, float),
            "end": np.array([False] * (n - 1) + [True])}

# A call entered at 100 (bar 0): stop 90, T1 120, T2 140. The line rises under the price, then the price dips to 97.
A = arrays(hi=[100, 104, 106, 106, 100], lo=[99, 101, 103, 97, 95], cl=[100, 103, 105, 98, 96])
tr = {"side": "CE", "entry": 100.0, "stop": 90.0, "t1": 120.0, "t2": 140.0}
line = np.array([95.0, 96.0, 99.0, 99.0, 99.0])
plain = sss.simulate(A, 0, tr, line, "t2", follow_from_entry=False)
check("without the follow: exactly simulate_live", plain == ows.simulate_live(A, 0, tr, line, "t2"), plain)
check("...today's stop at 90 is never touched; out at the day's close", plain[-1][:2] == (96.0, 4), plain)
fol = sss.simulate(A, 0, tr, line, "t2", follow_from_entry=True)
check("following: the stop climbs to bar 2's line (99) and is hit on bar 3's dip", fol[-1][:2] == (99.0, 3), fol)

wrong = np.array([95.0, 110.0, 110.0, 110.0, 110.0])          # the line ABOVE a call's price: never moved to
w = sss.simulate(A, 0, tr, wrong, "t2", follow_from_entry=True)
check("a line on the wrong side is never moved to (no stop above the market)", w == plain, w)

nxt = np.array([95.0, 102.5, 95.0, 95.0, 95.0])               # bar 1's line 102.5, bar 1's own low 101
n1 = sss.simulate(A, 0, tr, nxt, "t2", follow_from_entry=True)
check("a candle's line applies from the next candle, not its own (bar 1 not stopped by its own line; bar 2 holds above it, bar 3 hits it)",
      n1[-1][1] != 1 and n1[-1][:2] == (102.5, 3), n1)

put = {"side": "PE", "entry": 100.0, "stop": 110.0, "t1": 80.0, "t2": 60.0}
B = arrays(hi=[100, 99, 97, 103, 104], lo=[99, 96, 94, 96, 100], cl=[100, 97, 95, 102, 103])
pline = np.array([105.0, 104.0, 101.0, 101.0, 101.0])
pf = sss.simulate(B, 0, put, pline, "t2", follow_from_entry=True)
check("a put: the stop comes DOWN with the line above the price and is hit on the bounce", pf[-1][:2] == (101.0, 3), pf)

print()
print("STOP SUPERTREND STUDY TEST PASSED" if not fails else f"STOP SUPERTREND STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
