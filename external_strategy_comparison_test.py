#!/usr/bin/env python3
"""external_strategy_comparison.py's simulate_exit() and htf_trend() -
hand-traced, fake arrays only. The exit priority (stop -> breakeven ->
Supertrend trail -> EMA cross -> square-off) and the look-ahead-safe HTF
shift are the two genuinely new, error-prone pieces this port adds; the
Supertrend/ADX/ATR/EMA formulas themselves are this project's own
already-used functions, not retested here."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

import external_strategy_comparison as ext

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


P = ext.PARAMS["NIFTY"]
T0900 = dt.time(9, 30)


def feats(hi, lo, cl, ema_f, ema_s, st_line):
    return {"hi": np.array(hi, float), "lo": np.array(lo, float), "cl": np.array(cl, float),
            "ema_f": np.array(ema_f, float), "ema_s": np.array(ema_s, float),
            "st_line": np.array(st_line, float)}


def times_at(hhmm_list):
    return [dt.time(int(h), int(m)) for h, m in (s.split(":") for s in hhmm_list)]


print("1. THE ORIGINAL STOP FIRES BEFORE BREAKEVEN IS EVER TOUCHED")
f = feats(hi=[101, 99], lo=[95, 89], cl=[98, 90], ema_f=[10, 10], ema_s=[9, 9], st_line=[90, 90])
t = times_at(["10:00", "10:15"])
legs = ext.simulate_exit(f, t, "CE", 0, 100.0, 10.0, P, 2)
check("stops at the original stop (100 - 1.5*10 = 85) - never reached here, so falls to data end",
      legs == [(90.0, 1, 1.0)], legs)

print("2. THE STOP IS HONOURED EVEN BEFORE BREAKEVEN - A DEEP DROP STOPS OUT AT THE ORIGINAL LEVEL")
f = feats(hi=[101], lo=[80], cl=[82], ema_f=[10], ema_s=[9], st_line=[80])
t = times_at(["10:00"])
legs = ext.simulate_exit(f, t, "CE", 0, 100.0, 10.0, P, 1)
check("stops at 85 (100 - 1.5*10), the original stop, bar 0", legs == [(85.0, 0, 1.0)], legs)

print("3. BREAKEVEN TRIGGERS, THEN THE SUPERTREND LINE TAKES OVER AS THE TRAIL")
# bar0: high(111) clears the breakeven trigger (100+1*10=110) -> stop to 100, then
# max(100, st_line=90)=100 (no change - the ST line sits below breakeven here).
# bar1: nothing fires; trail check max(100, st_line=95)=100 (still below breakeven).
# bar2: low(90) <= the still-100 stop -> stops out AT BREAKEVEN (100), not at 95 -
# the Supertrend line never actually got ABOVE breakeven in this fixture, so it
# never tightened the stop past 100.
f = feats(hi=[111, 108, 95], lo=[105, 102, 90], cl=[108, 103, 92],
          ema_f=[10, 10, 10], ema_s=[9, 9, 9], st_line=[90, 95, 95])
t = times_at(["10:00", "10:15", "10:30"])
legs = ext.simulate_exit(f, t, "CE", 0, 100.0, 10.0, P, 3)
check("stops out at breakeven (100) in bar 2 - the Supertrend line (90, 95) never rose "
      "above breakeven in this fixture, so it never tightened the stop past it",
      legs == [(100.0, 2, 1.0)], legs)

print("3b. ...AND ONCE THE SUPERTREND LINE DOES RISE ABOVE BREAKEVEN, IT ACTUALLY TIGHTENS THE STOP")
# Same shape, but bar1's Supertrend line (103) is now ABOVE breakeven (100) - the
# trail should pick that up, so bar2's pullback to 90 stops out at 103, not 100.
f = feats(hi=[111, 108, 95], lo=[105, 102, 90], cl=[108, 103, 92],
          ema_f=[10, 10, 10], ema_s=[9, 9, 9], st_line=[90, 103, 103])
legs = ext.simulate_exit(f, t, "CE", 0, 100.0, 10.0, P, 3)
check("stops out at 103 - the trail actually tightened past breakeven once the Supertrend "
      "line itself rose above it", legs == [(103.0, 2, 1.0)], legs)

print("4. THE EMA CROSS CLOSES THE TRADE EVEN WHEN THE STOP IS NOWHERE NEAR")
f = feats(hi=[108, 109], lo=[103, 104], cl=[106, 105], ema_f=[10, 8], ema_s=[9, 9], st_line=[90, 90])
t = times_at(["10:00", "10:15"])
legs = ext.simulate_exit(f, t, "CE", 0, 100.0, 10.0, P, 2)
check("bar 0: ema_f(10) > ema_s(9), no cross yet - stop (85) nowhere close, holds",
      legs[-1][1] == 1, legs)
check("bar 1: ema_f(8) < ema_s(9) - crossed against the long - closes at that bar's close (105)",
      legs == [(105.0, 1, 1.0)], legs)

print("5. THE 15:15 SQUARE-OFF CLOSES THE TRADE EVEN IF NOTHING ELSE WOULD HAVE")
f = feats(hi=[108, 109], lo=[103, 104], cl=[106, 107], ema_f=[10, 10], ema_s=[9, 9], st_line=[90, 90])
t = times_at(["15:00", "15:15"])
legs = ext.simulate_exit(f, t, "CE", 0, 100.0, 10.0, P, 2)
check("bar 0 (15:00): nothing fires, still before square-off", legs[-1][1] == 1, legs)
check("bar 1 (15:15): square-off fires, closes at that bar's own close (107)",
      legs == [(107.0, 1, 1.0)], legs)

print("6. THE MIRROR FOR A SHORT (PE): BREAKEVEN AND THE TRAIL MOVE DOWNWARD, NOT UP")
# bar0: low(88) clears the short's breakeven trigger (100-1*10=90) -> stop to 100,
# then min(100, st_line=110)=100 (no tightening - 110 is LOOSER than breakeven for a short).
# bar1: nothing fires; trail min(100, st_line=105)=100 (still looser, no change).
# bar2: high(101) >= the still-100 stop -> stops out AT BREAKEVEN (100).
f = feats(hi=[95, 92, 101], lo=[88, 85, 97], cl=[90, 87, 99],
          ema_f=[9, 9, 9], ema_s=[10, 10, 10], st_line=[110, 105, 105])
t = times_at(["10:00", "10:15", "10:30"])
legs = ext.simulate_exit(f, t, "PE", 0, 100.0, 10.0, P, 3)
check("stops out at breakeven (100) in bar 2 - the Supertrend line (110, 105) stayed "
      "looser than breakeven throughout, so it never tightened the stop",
      legs == [(100.0, 2, 1.0)], legs)

print("6b. ...AND ONCE THE SUPERTREND LINE DOES FALL BELOW BREAKEVEN, IT TIGHTENS THE SHORT'S STOP TOO")
f = feats(hi=[95, 92, 95], lo=[88, 85, 91], cl=[90, 87, 93],
          ema_f=[9, 9, 9], ema_s=[10, 10, 10], st_line=[110, 94, 94])
legs = ext.simulate_exit(f, t, "PE", 0, 100.0, 10.0, P, 3)
check("stops out at 94 - the trail tightened below breakeven once the Supertrend line itself fell there",
      legs == [(94.0, 2, 1.0)], legs)

print("7. htf_trend(): LOOK-AHEAD SAFETY - CHANGING ONLY THE LAST BAR'S OWN FUTURE PRICE "
      "MUST NEVER CHANGE AN EARLIER BAR'S READING")
# 90 trading days of steadily rising 15-minute closes - plenty for EMA(20)/EMA(50)
# on the resampled 1-hour series (htf_mult=4) to be fully warmed up well before the end.
idx = pd.date_range("2026-01-01 09:15", periods=90 * 25, freq="15min", tz="Asia/Kolkata")
rising = 20000 + np.arange(len(idx)) * 0.5
df_a = pd.DataFrame({"Close": rising}, index=idx)
df_b = df_a.copy()
# Perturb ONLY the very last bar's close, wildly - if the shift+reindex alignment
# ever let a bar see data from its own still-forming (or any later) coarse window,
# this single change could ripple backwards and change earlier readings.
df_b.loc[df_b.index[-1], "Close"] = 1.0
d_a = ext.htf_trend(df_a, P)
d_b = ext.htf_trend(df_b, P)
check("every reading except (at most) the very last coarse window's own is identical "
      "between the two runs - the last bar's wild change cannot reach backwards "
      "(.equals(), not == - a shared warmup NaN must not read as a mismatch)",
      d_a.iloc[:-4].equals(d_b.iloc[:-4]), (d_a.iloc[-8:], d_b.iloc[-8:]))
check("a long, steady uptrend with plenty of warmup eventually reads UP (1) well before the end",
      (d_a.iloc[-20:-4] == 1).all(), d_a.iloc[-20:])

print("EXTERNAL STRATEGY COMPARISON TEST PASSED" if not fails else f"EXTERNAL STRATEGY COMPARISON TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
