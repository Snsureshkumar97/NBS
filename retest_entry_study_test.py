#!/usr/bin/env python3
"""retest_entry_study.retest on hand-made 5-minute candles: a touch of the level and a close back on the trade's side
enters at that close; the stop first, no touch inside the window, or the next day's candles mean no trade; the entry's
own 15-minute candle and the 5-minute candles left in it are found."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd

import retest_entry_study as rt

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

T = lambda hm, d="2026-10-07": pd.Timestamp(f"{d} {hm}", tz="Asia/Kolkata")
df = pd.DataFrame({"Close": [100.0] * 6}, index=pd.DatetimeIndex([T("09:15"), T("09:30"), T("09:45"), T("10:00"), T("10:15"), T("10:30")]))
def five(rows, start="09:30", d="2026-10-07"):
    idx = pd.date_range(f"{d} {start}", periods=len(rows), freq="5min", tz="Asia/Kolkata")
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)
CE = {"side": "CE", "entry": 100.0, "stop": 90.0}          # signal off the 09:15 candle: in at 09:30, 100

# 09:30 runs up, 09:35 dips to 99.5 (touch) and closes 101 (holds) -> in at 101, 09:40
d5 = five([[100, 103, 100.5, 102.5], [102.5, 102.6, 99.5, 101.0], [101, 104, 100.8, 103], [103, 105, 102, 104]])
got = rt.retest(CE, 0, df, d5, atr=4.0, depth=0.0, window=30)
check("a touch and a close back above: in at that close, at its candle's end", got is not None and got[0] == T("09:40") and got[1] == 101.0, got and got[:2])
check("...in the 09:30 15-minute candle, with its last 5-minute candle still to walk", got[2] == 1 and len(got[3]) == 1, got and (got[2], len(got[3])))
d5 = five([[100, 103, 100.5, 102.5], [102.5, 102.6, 99.5, 99.8], [99.8, 101, 99.6, 100.4]])
got = rt.retest(CE, 0, df, d5, atr=4.0, depth=0.0, window=30)
check("touched, closed below (not holding), then a candle closes back above: in there", got is not None and got[0] == T("09:45") and got[1] == 100.4, got and got[:2])
check("...that candle ends its 15-minute candle: nothing left to walk in it, the walk starts at the next", got[2] == 1 and len(got[3]) == 0, got and (got[2], len(got[3])))
d5 = five([[100, 103, 100.5, 102.5], [102.5, 102.6, 89.0, 95.0], [95, 101, 94, 100.5]])
check("the stop touched before it held: no trade", rt.retest(CE, 0, df, d5, atr=4.0, depth=0.0, window=30) is None)
d5 = five([[100, 103, 100.5, 102.5], [102.5, 106, 102, 105], [105, 108, 104, 107], [107, 110, 106, 109]])
check("it ran and never came back inside the window: no trade (the guide's own warning)", rt.retest(CE, 0, df, d5, atr=4.0, depth=0.0, window=30) is None)
d5 = five([[100, 103, 100.5, 102.5], [102.5, 102.6, 99.5, 101.0]])
check("deeper level (0.25 ATR = 1 point, at 99): 99.5 is not a touch", rt.retest(CE, 0, df, d5, atr=4.0, depth=0.25, window=30) is None)
d5 = five([[100, 103, 100.5, 102.5], [102.5, 102.6, 98.8, 99.4]])
got = rt.retest(CE, 0, df, d5, atr=4.0, depth=0.25, window=30)
check("...98.8 touches 99 and closes back above it at 99.4: in at 99.4, cheaper than the signal", got is not None and got[1] == 99.4, got and got[:2])
PE = {"side": "PE", "entry": 100.0, "stop": 110.0}
d5 = five([[100, 99.5, 97, 98], [98, 100.4, 97.5, 99.0]])
got = rt.retest(PE, 0, df, d5, atr=4.0, depth=0.0, window=30)
check("a put: up to the level and back below it", got is not None and got[1] == 99.0, got and got[:2])
late = pd.DataFrame({"Close": [100.0, 100.0]}, index=pd.DatetimeIndex([T("15:00"), T("15:15")]))
d5 = pd.concat([five([[100, 103, 100.5, 102.5]], start="15:15"), five([[102, 102, 99, 101]], start="09:15", d="2026-10-08")])
check("never into the next day", rt.retest(CE, 0, late, d5, atr=4.0, depth=0.0, window=60) is None)

print()
print("RETEST ENTRY STUDY TEST PASSED" if not fails else f"RETEST ENTRY STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
