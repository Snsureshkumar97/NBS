#!/usr/bin/env python3
"""pasted_combos_study.run on hand-made 15-minute candles with the indicators pinned: Combination 2 enters on the candle
where every condition FIRST holds (not again while they stay true), its stop is the last N candles' low and its target
rr x that risk; Combination 1 needs the EMA cross, the close beyond VWAP and RSI past 60 / 40 together."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

import pasted_combos_study as pc

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

PIN = {}
pc.ind.ema = lambda s, n: pd.Series(PIN[f"e{n}"], index=s.index)
pc.ind.vwap = lambda df: pd.Series(PIN["vw"], index=df.index)
pc.ind.adx = lambda df, n=14: pd.Series(PIN["adx"], index=df.index)
pc.ind.plus_minus_di = lambda df, n=14: (pd.Series(PIN["pdi"], index=df.index), pd.Series(PIN["mdi"], index=df.index))
pc.ind.rsi = lambda s, n=14: pd.Series(PIN["rsi"], index=s.index)
pc.ind.atr = lambda df, n=14: pd.Series(np.full(len(df), 10.0), index=df.index)
pc.vea.price_trade = lambda k, days, t_in, s_in, t_out, s_out, sig, call: (s_out - s_in) * (1 if call else -1)

idx = pd.date_range("2026-10-07 09:15", periods=25, freq="15min", tz="Asia/Kolkata")
idx = idx.append(pd.date_range("2026-10-08 09:15", periods=25, freq="15min", tz="Asia/Kolkata"))
idx = idx.append(pd.date_range("2026-10-09 09:15", periods=25, freq="15min", tz="Asia/Kolkata"))
n = len(idx)
day3 = 50                                             # 09:15 on the third day (past the 60-candle warm-up from index 60)
close = np.full(n, 100.0); lo = close - 2; hi = close + 2
lo[62:65] = [95.0, 96.0, 97.0]                        # entry at 65: its last 5 (61..65) reach 95, its last 3 (63..65) only 96
df = pd.DataFrame({"Open": close, "High": hi, "Low": lo, "Close": close, "Volume": 0.0}, index=idx)
on = np.zeros(n, bool); on[65:70] = True             # all conditions hold from candle 65 to 69
PIN.update(e50=np.where(on, 90.0, 110.0), vw=np.where(on, 95.0, 105.0), adx=np.where(on, 25.0, 10.0) + np.arange(n) * 0.01,
           pdi=np.full(n, 30.0), mdi=np.full(n, 10.0))
hi[67] = 111.0                                        # target = 100 + 2.0 x (100 - 95) = 110 -> hit on candle 67
df["High"] = hi
r = pc.run("NIFTY", df, {d: 0.12 for d in set(idx.date)}, set(), 2, 15, adx_min=20.0, rr=2.0, swing=5)
check("one trade, entered on the FIRST candle where everything holds (65), not again while it stays true",
      len(r) == 1 and r[0]["when"] == idx[65] + pd.Timedelta(minutes=15), [x["when"] for x in r])
check("its stop the last 5 candles' low (95) and target 2R (110): out at the target", r and r[0]["closed_via"] == "target"
      and abs(r[0]["net"] - 10.0) < 1e-9, r)
r3 = pc.run("NIFTY", df, {d: 0.12 for d in set(idx.date)}, set(), 2, 15, adx_min=20.0, rr=2.0, swing=3)
check("with the last 3 candles the stop is 96 (risk 4) and the target 108", r3 and abs(r3[0]["net"] - 8.0) < 1e-9, r3)

PIN.update(e9=np.where(np.arange(n) >= 66, 101.0, 99.0), e21=np.full(n, 100.0), vw=np.full(n, 95.0), rsi=np.full(n, 65.0))
c1 = pc.run("NIFTY", df, {d: 0.12 for d in set(idx.date)}, set(), 1, 15)
check("Combination 1: the cross at 66 with the close above VWAP and RSI 65: a call", len(c1) == 1 and c1[0]["side"] == "CE"
      and c1[0]["when"] == idx[66] + pd.Timedelta(minutes=15), c1)
PIN["rsi"] = np.full(n, 55.0)
check("...with RSI 55 (not past 60): no trade", pc.run("NIFTY", df, {d: 0.12 for d in set(idx.date)}, set(), 1, 15) == [])

print()
print("PASTED COMBOS STUDY TEST PASSED" if not fails else f"PASTED COMBOS STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
