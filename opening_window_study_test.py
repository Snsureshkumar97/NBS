#!/usr/bin/env python3
"""opening_window_study.py's exit and window gate, hand-traced on made-up candles."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

import opening_window_study as ows

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def arrays(hi, lo, cl, end_at=None):
    n = len(cl)
    end = np.zeros(n, dtype=bool)
    if end_at is not None:
        end[end_at] = True
    return {"hi": np.array(hi, float), "lo": np.array(lo, float), "cl": np.array(cl, float), "end": end}

NAN = np.full(30, np.nan)
CE = {"side": "CE", "entry": 100.0, "stop": 90.0, "t1": 105.0, "t2": 110.0, "t3": 115.0}

print("1. THE 2-HOUR BREAKEVEN, PRICED AS IT FILLS")
# Entry at bar 0; drifts down to 94 and sits there; never reaches the stop (90) or T1 (105).
flat = arrays([101] + [99] * 12, [99] + [93] * 12, [100] + [94] * 12)
legs = ows.simulate_live(flat, 0, CE, NAN, "t2", realistic_be=True)
check("price already under entry at 2 hours: sold at the market there (94), at bar 8", legs == [(94.0, 8, 1.0)], legs)
legs_old = ows.simulate_live(flat, 0, CE, NAN, "t2", realistic_be=False)
check("the old fill books it at the entry (100) - the optimism this study corrects", legs_old == [(100.0, 8, 1.0)], legs_old)
up = arrays([101] + [102] * 8 + [103, 99, 99, 99], [99] + [100.5] * 8 + [101, 98, 98, 98], [100] + [101] * 8 + [102, 99, 99, 99])
legs = ows.simulate_live(up, 0, CE, NAN, "t2")
check("price above entry at 2 hours: the stop moves to entry, hit later at entry", legs == [(100.0, 10, 1.0)], legs)

print("2. T1, THE TRAIL, THE TARGET, THE STOP, THE CLOSE")
t1 = arrays([101, 106, 107, 108, 108], [99, 101, 105.5, 106.6, 106], [100, 105, 106, 107, 106.2])
st = np.array([np.nan, 100.0, 104.0, 106.5, 106.5])
legs = ows.simulate_live(t1, 0, CE, st, "t2")
check("T1 touched -> stop to T1 (105), trailed up the Supertrend to 106.5, hit there", legs == [(106.5, 4, 1.0)], legs)
tgt = arrays([101, 106, 111], [99, 101, 105.5], [100, 105, 110])
check("T2 reached: out at T2", ows.simulate_live(tgt, 0, CE, NAN, "t2") == [(110.0, 2, 1.0)])
dip = arrays([101, 106, 107], [99, 101, 104], [100, 105, 106])
check("after T1, a dip under T1 stops it out AT T1 (the ratchet)", ows.simulate_live(dip, 0, CE, NAN, "t2") == [(105.0, 2, 1.0)])
both = arrays([101, 111], [99, 89], [100, 95])
check("stop and target in one candle: the stop (pessimistic)", ows.simulate_live(both, 0, CE, NAN, "t2") == [(90.0, 1, 1.0)])
close = arrays([101, 102, 103], [99, 99, 100], [100, 101, 102], end_at=2)
check("the day ends first: out at its close", ows.simulate_live(close, 0, CE, NAN, "t2") == [(102.0, 2, 1.0)])
PE = {"side": "PE", "entry": 100.0, "stop": 110.0, "t1": 95.0, "t2": 90.0, "t3": 85.0}
pe = arrays([101] + [107] * 12, [99] + [101] * 12, [100] + [106] * 12)
check("a PE mirrored: above entry at 2 hours -> sold at the market (106)",
      ows.simulate_live(pe, 0, PE, NAN, "t2") == [(106.0, 8, 1.0)])

print("3. THE WINDOW GATE")
idx = pd.DatetimeIndex(pd.to_datetime(["2026-10-05 09:15", "2026-10-05 09:30", "2026-10-05 09:45", "2026-10-05 10:00"])
                       ).tz_localize("Asia/Kolkata")
df = pd.DataFrame({"Close": [1, 2, 3, 4]}, index=idx)
seen = []
base = lambda i, rec: seen.append(i) or True
g15, g30 = ows.window_gate(base, df, 15), ows.window_gate(base, df, 30)
check("skip 15: the 09:15 candle blocked, 09:30 on allowed", [g15(i, {}) for i in range(4)] == [False, True, True, True])
check("skip 30: 09:15 and 09:30 blocked", [g30(i, {}) for i in range(4)] == [False, False, True, True])
seen.clear()
g30(0, {})
check("...a blocked candle never even asks the live checks", seen == [])

print()
print("OPENING WINDOW STUDY TEST PASSED" if not fails else f"OPENING WINDOW STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
