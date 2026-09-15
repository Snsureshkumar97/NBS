#!/usr/bin/env python3
"""The two drawdown controls in drawdown_controls_study.py, checked by hand: the
breaker sees only trades already closed, pauses at the line and resumes once
recovered; the VIX rule uses yesterday against the year before it."""
import datetime as dt

import pandas as pd

import drawdown_controls_study as dc

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

T = lambda day, h: dt.datetime(2026, 1, day, h)
tr = lambda day, h_in, h_out, net: {"when": T(day, h_in), "exit_time": T(day, h_out), "net": net}

print("1. CIRCUIT BREAKER")
trades = [tr(5, 10, 11, -60_000), tr(5, 12, 13, -50_000),   # closed: shadow drawdown 110k
          tr(5, 14, 15, +40_000),                            # entered at 14:00 -> paper (paused)
          tr(6, 10, 11, +30_000),                            # closed by now: -60k -50k +40k -> 70k down, still paused
          tr(6, 12, 13, -10_000)]                            # closed by now: +30k too -> 40k down -> resumed
w = dc.breaker_weights(trades, stop=100_000, resume=50_000)
check("the first two trades are taken - nothing had closed below the line", w[0] == 1 and w[1] == 1, w)
check("after 1,10,000 of closed losses the next entry is paper", w[2] == 0, w)
check("still 70,000 below the high (the paper win counts, the open trade does not): paused", w[3] == 0, w)
check("once the shadow curve is back within 50,000 of its high, trading resumes", w[4] == 1, w)
overlap = [tr(7, 10, 15, -150_000), tr(7, 11, 12, +5_000)]
w2 = dc.breaker_weights(overlap, stop=100_000, resume=50_000)
check("a loss still open at the next entry does not count yet - no look-ahead", w2 == [1.0, 1.0], w2)
check("hysteresis: between 50,000 and 1,00,000 below the high, a paused breaker stays paused",
      dc.breaker_weights([tr(8, 10, 11, -120_000), tr(8, 12, 13, +40_000), tr(8, 14, 15, 0), tr(8, 16, 17, 0)],
                         stop=100_000, resume=50_000)[2:] == [0.0, 0.0])

print("2. HALF SIZE WHEN VIX IS HIGH")
days = pd.bdate_range("2024-01-01", periods=300).date
vix = pd.Series([12.0] * 299 + [30.0], index=days)
w = dc.vix_weights([days[-1]], vix, q=0.75, window=250)
check("today's own spike is not known yet - yesterday's calm VIX gives full size", w == [1.0], w)
vix2 = pd.Series([12.0] * 298 + [30.0, 12.0], index=days)
w = dc.vix_weights([days[-1]], vix2, q=0.75, window=250)
check("yesterday's spike above the year's 75th percentile gives half size", w == [0.5], w)
check("without a year of history, full size", dc.vix_weights([days[10]], vix2) == [1.0])

print("DRAWDOWN CONTROLS TEST PASSED" if not fails else f"DRAWDOWN CONTROLS TEST FAILED: {fails}")
