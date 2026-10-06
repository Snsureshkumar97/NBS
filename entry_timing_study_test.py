#!/usr/bin/env python3
"""entry_timing_study.py's pieces, hand-traced: the forming candles, the three entry variants, the 5-minute exit, and
pricing identical to pro_study.price()."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

import entry_timing_study as ets

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

IST = "Asia/Kolkata"
def t(s):
    return pd.Timestamp(f"2026-10-05 {s}", tz=IST)

print("1. THE FORMING CANDLE, FROM ITS OWN 5-MINUTE CANDLES")
idx5 = pd.DatetimeIndex([t("09:15"), t("09:20"), t("09:25")])
df5 = pd.DataFrame({"Open": [100, 104, 103], "High": [105, 108, 104], "Low": [99, 102, 97], "Close": [104, 103, 98],
                    "Volume": [0, 0, 0]}, index=idx5).astype(float)
p = ets.partial_rows(None, df5)[t("09:15")]
check("after 5 minutes: the first 5-minute candle", p[0] == (100.0, 105.0, 99.0, 104.0, 0.0), p[0])
check("after 10 minutes: open of the first, high/low of both, close of the second", p[1] == (100.0, 108.0, 99.0, 103.0, 0.0), p[1])

print("2. THE THREE ENTRY VARIANTS")
R = lambda side: (side, 100.0, 95.0, 101.0, 102.0, 103.0, 25.0)
sigs = [(t("09:20"), 5, 1, R("CE")), (t("09:25"), 5, 2, None), (t("09:30"), 5, 3, R("CE")),
        (t("09:35"), 6, 1, R("CE")), (t("09:40"), 6, 2, R("PE")), (t("09:45"), 6, 3, R("PE"))]
first = [(s[0], s[2]) for s in ets.select(sigs, "first signal")]
check("first signal: every read that says enter", first == [(t("09:20"), 1), (t("09:30"), 3), (t("09:35"), 1), (t("09:40"), 2), (t("09:45"), 3)], first)
close = [(s[0], s[2]) for s in ets.select(sigs, "candle close")]
check("candle close: only the reads at a 15-minute close", close == [(t("09:30"), 3), (t("09:45"), 3)], close)
conf = [(s[0], s[2]) for s in ets.select(sigs, "confirmed")]
check("confirmed: only when the read 5 minutes earlier said the SAME side", conf == [(t("09:35"), 1), (t("09:45"), 3)], conf)

print("3. THE EXIT, ON 5-MINUTE CANDLES")
def bars(rows, start="09:30"):
    ts = pd.date_range(t(start), periods=len(rows), freq="5min")
    return pd.DataFrame(rows, columns=["High", "Low", "Close"], index=ts).assign(Open=lambda d: d["Close"], Volume=0.0)
nan_st = lambda ts: float("nan")
r = ("CE", 100.0, 95.0, 101.0, 102.0, 103.0)
d = bars([(100.5, 94.0, 96.0)])
check("the stop: out at the stop, at the candle's end", ets.simulate(d, nan_st, t("09:30"), r, "t2") == [(95.0, t("09:35"))])
d = bars([(102.5, 99.0, 102.0)])
check("T2 reached: out at T2", ets.simulate(d, nan_st, t("09:30"), r, "t2") == [(102.0, t("09:35"))])
d = bars([(101.2, 99.5, 101.0), (101.5, 101.1, 101.2), (101.6, 100.5, 101.0)])
check("T1 touched -> the stop to T1; a later dip under it: out at T1", ets.simulate(d, nan_st, t("09:30"), r, "t2") == [(101.0, t("09:45"))])
d = bars([(101.2, 99.5, 101.0), (101.5, 101.1, 101.4), (101.9, 101.5, 101.6), (101.7, 101.3, 101.4)])
st = lambda ts: 101.45 if ts >= t("09:40") else float("nan")
check("...then it trails the Supertrend (101.45), hit on a later candle", ets.simulate(d, st, t("09:30"), r, "t2") == [(101.45, t("09:50"))])
d = bars([(100.5, 99.0, 99.5)] * 30)
check("2 hours (24 candles) with no T1 and the price under entry: sold at the market there",
      ets.simulate(d, nan_st, t("09:30"), r, "t2") == [(99.5, t("09:30") + pd.Timedelta(minutes=120))])
d = bars([(100.8, 100.2, 100.5)] * 24 + [(100.6, 99.9, 100.0)])
check("...over entry at 2 hours: the stop moves to entry, hit next", ets.simulate(d, nan_st, t("09:30"), r, "t2")
      == [(100.0, t("09:30") + pd.Timedelta(minutes=125))])
d = pd.concat([bars([(100.5, 99.5, 100.2)] * 3, "15:15"),
               bars([(100.5, 99.5, 100.4)], "09:15").set_axis([pd.Timestamp("2026-10-06 09:15", tz=IST)])])
check("the day ends first: out at its last close", ets.simulate(d, nan_st, t("15:15"), r, "t2") == [(100.2, t("15:30"))])

print("4. PRICING = pro_study.price()")
import pro_study as ps
idx15 = pd.date_range(t("09:15"), periods=20, freq="15min")
df15 = pd.DataFrame({"Close": np.linspace(24000, 24100, 20)}, index=idx15)
A = {"days": {pd.Timestamp("2026-10-05").date(), pd.Timestamp("2026-10-06").date(), pd.Timestamp("2026-10-07").date()}}
tr = {"side": "CE", "entry": 24020.0}
legs15 = [(24080.0, 6, 1.0)]
want = ps.price("NIFTY", df15, A, 2, tr, legs15, 0.15)["net"]
got = ets.price("NIFTY", idx15[2] + pd.Timedelta(minutes=15), 24020.0, "CE", [(24080.0, idx15[6] + pd.Timedelta(minutes=15))],
                0.15, A["days"])
check("the same money for the same trade", abs(got - want) < 1e-6, (got, want))

print()
print("ENTRY TIMING STUDY TEST PASSED" if not fails else f"ENTRY TIMING STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
