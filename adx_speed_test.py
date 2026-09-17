#!/usr/bin/env python3
"""The faster trend measure adopted 17 Sep 2026: exactly what was tested, and
only where it was tested.

trend_speed_study.py variant B: +DI/-DI over 14 candles, DX averaged over 3
instead of 14. Adopted for the three Indian indices; BTC keeps Wilder's ADX,
because it was never measured with B.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

import config
import indicators as ind
import signal_engine as se
import trend_speed_study as ts

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


rng = np.random.RandomState(7)
idx = pd.date_range("2026-09-01 09:15", periods=400, freq="15min", tz="Asia/Kolkata")
close = 23000 + np.cumsum(rng.randn(400) * 12 + np.r_[np.zeros(300), np.full(100, 6.0)])   # flat, then a rally
df = pd.DataFrame({"Open": close - 3, "High": close + 10, "Low": close - 10, "Close": close, "Volume": 1000}, index=idx)

print("1. THE MEASURE")
wilder = ind.adx(df, 14)
check("no DX length is Wilder's ADX, unchanged - the screener still gets that",
      np.allclose(wilder.to_numpy(), ind.adx(df, 14, None).to_numpy(), equal_nan=True))
check("the adopted setting is exactly the tested variant B",
      np.allclose(ind.adx(df, 14, 3).to_numpy(), ts.fast_adx(df, 14, 3).to_numpy(), equal_nan=True))
check("it follows a new trend faster, which is the point",
      float(ind.adx(df, 14, 3).iloc[315]) > float(wilder.iloc[315]),
      f"{float(ind.adx(df, 14, 3).iloc[315]):.1f} vs {float(wilder.iloc[315]):.1f} fifteen candles into the rally")

print("2. ONLY WHERE IT WAS TESTED")
for k in ("NIFTY", "BANKNIFTY", "SENSEX"):
    check(f"{k} uses DX over 3", config.adx_dx_smoothing(k) == 3)
check("BTC keeps Wilder's ADX - never measured with the faster one", config.adx_dx_smoothing("BTC") is None)
check("no instrument named means the default market (the NSE-only desktop and main.py)",
      config.adx_dx_smoothing() == 3)
check("the engine's gate reads it per instrument",
      se.compute_technical_signal(df, "NIFTY")["adx"] == round(float(ind.adx(df, 14, 3).iloc[-1]), 1)
      and se.compute_technical_signal(df, "BTC")["adx"] == round(float(wilder.iloc[-1]), 1))

print("3. EVERY PATH PASSES THE INSTRUMENT")
here = os.path.dirname(os.path.abspath(__file__))
src = {f: open(os.path.join(here, f)).read() for f in ("feeds.py", "main.py", "gui.py", "backtest_intraday.py", "signal_engine.py")}
check("the website's live recompute", "compute_technical_signal(df, name)" in src["feeds.py"]
      and "compute_market_trend(df, name)" in src["feeds.py"])
check("the full analysis pass (BTC goes through it too)", "compute_technical_signal(df, index_key)" in src["main.py"]
      and "compute_market_trend(df, index_key)" in src["main.py"])
check("the desktop app", "compute_technical_signal(df, key)" in src["gui.py"] and "compute_market_trend(df, key)" in src["gui.py"])
check("the backtest, so a study measures what runs live",
      "pre = precompute(df, index_key)" in src["backtest_intraday.py"]
      and "config.adx_dx_smoothing(index_key)" in src["backtest_intraday.py"])
check("no call left reading a global setting", "config.ADX_DX_SMOOTHING)" not in src["signal_engine.py"]
      and "config.ADX_DX_SMOOTHING)" not in src["backtest_intraday.py"])

print("ADX SPEED TEST PASSED" if not fails else f"ADX SPEED TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
