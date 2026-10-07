#!/usr/bin/env python3
"""vwap_ema_atr_study.run_index on hand-made candles, with the indicators pinned: the crossover only counts on a candle
entirely beyond VWAP; in at its close; stop 1.5 ATR before target 3 ATR inside a candle; a gap fills at the open; out
by 15:15; nothing before 09:30; one position at a time; the paste's two-stops brake."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

import vwap_ema_atr_study as v

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

PIN = {}
v.ind.ema = lambda s, n: pd.Series(PIN["e9" if n == 9 else "e20"], index=s.index)
v.ind.atr = lambda df, n=14: pd.Series(PIN["atr"], index=df.index)
v.ind.vwap = lambda df: pd.Series(PIN["vw"], index=df.index)
v.price_trade = lambda k, days, t_in, s_in, t_out, s_out, sig, call: (s_out - s_in) * (1 if call else -1)   # index points

def day(start="09:00", n=80, d="2026-10-07"):
    return pd.date_range(f"{d} {start}", periods=n, freq="5min", tz="Asia/Kolkata")
def frame(idx, close, lo=None, hi=None, op=None):
    close = np.asarray(close, float)
    return pd.DataFrame({"Open": close if op is None else op, "High": close + 1 if hi is None else hi,
                         "Low": close - 1 if lo is None else lo, "Close": close, "Volume": 0.0}, index=idx)
def setup(n, cross_at, vw=90.0, e_before=(99.0, 100.0), e_after=(101.0, 100.0), atr=10.0):
    e9 = np.array([e_before[0]] * cross_at + [e_after[0]] * (n - cross_at))
    e20 = np.array([e_before[1]] * cross_at + [e_after[1]] * (n - cross_at))
    PIN.update(e9=e9, e20=e20, atr=np.full(n, atr), vw=np.full(n, vw))

idx = day(); n = len(idx)                       # 09:00 .. 15:35
at = list(idx).index(pd.Timestamp("2026-10-07 12:00", tz="Asia/Kolkata"))   # past the 30-candle indicator warm-up
close = np.full(n, 100.0); close[at + 3] = 131.0          # 3 candles after entry the high reaches the target (100 + 30)
df = frame(idx, close)
setup(n, at)
r = v.run_index("NIFTY", df, {idx[0].date(): 0.12}, set(), brake=False)
check("a crossover up on a candle above VWAP: a call, in at its close, out at the 3 ATR target",
      len(r) == 1 and r[0]["side"] == "CE" and r[0]["when"] == idx[at] + pd.Timedelta(minutes=5) and r[0]["closed_via"] == "target"
      and abs(r[0]["net"] - 31.0) < 1e-9, r)          # the candle closes at 131 > target 130: it OPENED at 131 -> filled at 131
setup(n, at, vw=100.5)
check("the same crossover with the candle NOT entirely above VWAP (low 99 < 100.5): no trade",
      v.run_index("NIFTY", df, {idx[0].date(): 0.12}, set()) == [])
close = np.full(n, 100.0); lo = close - 1; lo[at + 2] = 84.0; hi = close + 1; hi[at + 2] = 131.0
setup(n, at)
r = v.run_index("NIFTY", frame(idx, close, lo=lo, hi=hi), {idx[0].date(): 0.12}, set())
check("stop and target in the same candle: the stop (1.5 ATR = 85) is taken", len(r) == 1 and r[0]["closed_via"] == "stop"
      and abs(r[0]["net"] - (-15.0)) < 1e-9, r)
op = close.copy(); op[at + 1] = 80.0; lo2 = close - 1; lo2[at + 1] = 79.0
r = v.run_index("NIFTY", frame(idx, close, lo=lo2, op=op), {idx[0].date(): 0.12}, set())
check("a candle that opens below the stop fills at its open (80), not at the stop", len(r) == 1 and abs(r[0]["net"] - (-20.0)) < 1e-9, r)
r = v.run_index("NIFTY", frame(idx, np.full(n, 100.0)), {idx[0].date(): 0.12}, set())
check("neither hit: out at the 15:10 candle's close (15:15)", len(r) == 1 and r[0]["closed_via"] == "square-off"
      and r[0]["exit_time"].strftime("%H:%M") == "15:15", r)
early = list(idx).index(pd.Timestamp("2026-10-07 09:20", tz="Asia/Kolkata"))
setup(n, early)
check("a crossover at 09:20: no trade (nothing before 09:30)", v.run_index("NIFTY", frame(idx, np.full(n, 100.0)), {idx[0].date(): 0.12}, set()) == [])
# one position at a time + the brake: crossovers flip every 2 candles from 10:00; each trade stopped next candle
flips = np.array([((k - at) // 2) % 2 for k in range(n)])
e9 = np.where(np.arange(n) < at, 99.0, np.where(flips == 0, 101.0, 99.5)); e20 = np.full(n, 100.0)
PIN.update(e9=e9, e20=e20, atr=np.full(n, 10.0), vw=np.full(n, 90.0))
close = np.full(n, 100.0); lo = close - 1
for k in range(at + 1, n, 4):
    lo[k] = 84.0                                  # the candle after each up-cross dips to the stop
r = v.run_index("NIFTY", frame(idx, close, lo=lo), {idx[0].date(): 0.12}, set())
rb = v.run_index("NIFTY", frame(idx, close, lo=lo), {idx[0].date(): 0.12}, set(), brake=True)
check("without the brake it keeps taking the crossovers", len(r) > 3, len(r))
check("with it: the day's first two both stopped -> no more trades that day", len(rb) == 2 and all(x["closed_via"] == "stop" for x in rb), len(rb))

print()
print("VWAP EMA ATR STUDY TEST PASSED" if not fails else f"VWAP EMA ATR STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
