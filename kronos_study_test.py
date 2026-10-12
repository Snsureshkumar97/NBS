#!/usr/bin/env python3
"""kronos_study.py's own arithmetic, checked on forecasts whose answer is known - never on Kronos's (12 Oct 2026):
the agreement rule for a call and a put; a candle with no forecast passes the filter unchanged; duplicate forecast
rows (a resumed run) are read once; and the skill score is 100% for a forecaster that knows the next hour, 0% for one
that always says the opposite - so a real result in between means what it says."""
import os
import shutil
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import kronos_study as ks

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

print("1. AGREEMENT")
up, down, flat = {"close": 100, "f4": 101}, {"close": 100, "f4": 99}, {"close": 100, "f4": 100}
check("a call agrees with a forecast up, not down or flat",
      ks.agrees(up, "f4", "CE") is True and ks.agrees(down, "f4", "CE") is False and ks.agrees(flat, "f4", "CE") is False)
check("a put agrees with a forecast down, not up or flat",
      ks.agrees(down, "f4", "PE") is True and ks.agrees(up, "f4", "PE") is False and ks.agrees(flat, "f4", "PE") is False)
check("no forecast: None, and the filter lets that candle pass unchanged",
      ks.agrees(None, "f4", "CE") is None and "return True if ok is None else ok" in open(ks.__file__).read())

print("2. THE FORECAST FILE")
real_out = ks.OUT
tmp = tempfile.mkdtemp()
try:
    ks.OUT = tmp
    pd.DataFrame({"i": [5, 6, 5], "time": ["a", "b", "a"], "close": [1, 2, 1], "f1": [1, 2, 3], "f2": [1, 2, 3],
                  "f3": [1, 2, 3], "f4": [1, 2, 9]}).to_csv(os.path.join(tmp, "SENSEX.csv"), index=False)
    f = ks.forecasts("SENSEX")
    check("a candle forecast twice (a resumed run) is read once, the later row", sorted(f) == [5, 6] and f[5]["f4"] == 9, f)

    print("3. THE SKILL SCORE, ON FORECASTERS WITH A KNOWN ANSWER")
    import backtest_intraday as bt
    df = bt.fetch_history("SENSEX", years=3, use_cache=True)
    cl = df["Close"].to_numpy()
    idx = range(400, len(cl) - 4)
    def write(sign):
        pd.DataFrame({"i": list(idx), "time": "x", "close": [cl[i] for i in idx],
                      **{f"f{h}": [cl[i] + sign * (cl[i + 4] - cl[i]) for i in idx] for h in (1, 2, 3, 4)}}
                     ).to_csv(os.path.join(tmp, "SENSEX.csv"), index=False)
    write(+1)
    s = ks.skill("SENSEX")
    check("a forecaster that knows the next hour scores 100% in both periods",
          s["is"]["kronos"] == 100.0 and s["oos"]["kronos"] == 100.0, (s["is"]["kronos"], s["oos"]["kronos"]))
    check("...and its agreement with the signal equals the signal's own hit rate (it IS the outcome)",
          abs(s["oos"]["agree"] - s["oos"]["signal"]) < 1e-9, (s["oos"]["agree"], s["oos"]["signal"]))
    check("the baselines are real hit rates, not 0 or 100", 30 < s["oos"]["signal"] < 70 and 30 < s["oos"]["last_hour"] < 70
          and 30 < s["oos"]["always_up"] < 70, s["oos"])
    check("thousands of candles a period: a 95% margin of about 2 points a period, per index",
          s["is"]["n"] > 2000 and s["oos"]["n"] > 1000 and s["oos"]["margin"] < 2.5, (s["is"]["n"], s["oos"]["n"]))
    write(-1)
    s = ks.skill("SENSEX")
    check("one that always says the opposite scores 0%", s["is"]["kronos"] == 0.0 and s["oos"]["kronos"] == 0.0,
          (s["is"]["kronos"], s["oos"]["kronos"]))
finally:
    ks.OUT = real_out
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("KRONOS STUDY TEST PASSED" if not fails else f"KRONOS STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
