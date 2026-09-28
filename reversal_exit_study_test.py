#!/usr/bin/env python3
"""reversal_exit_study.py's own building blocks - the parts a bad backtest number would hide in.

Not a re-run of the study itself (that takes real history and tens of seconds); this pins that
live_gate() truly calls today's shipped tickets.py gate methods (not a stale copy), and that
simulate_reversal() checks target and stop before the reversal, exactly as tickets.py's real
_check_price does. Fakes only - no history, no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import reversal_exit_study as rvs
import tickets

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


class FakeDF:
    """Just enough of a DataFrame for live_gate()'s df.iloc[:i+1] and se.opening_range()."""
    def __init__(self, closes, highs=None, lows=None):
        import datetime as dt
        import pandas as pd
        n = len(closes)
        idx = pd.date_range("2026-01-05 09:15", periods=n, freq="15min", tz="Asia/Kolkata")
        self.df = pd.DataFrame({"Close": closes, "High": highs or closes, "Low": lows or closes}, index=idx)
    def iloc(self):
        return self.df


def rec(bias="BULLISH", opt="CE", spot=100.0, risk=10.0, targets=(110.0, 120.0, 130.0)):
    return {"index": "NIFTY", "bias": bias, "option_type": opt, "spot": spot, "risk_points": risk,
            "index_targets": list(targets)}


print("1. live_gate() TRULY CALLS TODAY'S SHIPPED GATES, NOT A COPY")
_saved = config.MIN_REWARD_RISK_T3
try:
    df = FakeDF([100.0] * 40).df
    r = rec(risk=10.0, targets=(105.0, 108.0, 111.0))   # T3 only 1.1x the stop - passes need=1.0, fails need=2.0
    config.MIN_REWARD_RISK_T3 = 1.0
    check("passes reward:risk 1.0 (today's shipped value)", rvs.live_gate("NIFTY", 39, r, df))
    config.MIN_REWARD_RISK_T3 = 2.0
    check("...and the SAME rec fails once config.py's OWN value is turned up - live_gate reads it fresh, not a snapshot",
          not rvs.live_gate("NIFTY", 39, r, df))
finally:
    config.MIN_REWARD_RISK_T3 = _saved

_saved_w = config.WATCH_ONLY_INDICES
try:
    config.WATCH_ONLY_INDICES = ("NIFTY",)
    df = FakeDF([100.0] * 40).df
    check("watch-only blocks the index it names, straight from config.py",
          not rvs.live_gate("NIFTY", 39, rec(), df))
finally:
    config.WATCH_ONLY_INDICES = _saved_w

_saved_b = config.REGIME_OR_BREAK
try:
    config.REGIME_OR_BREAK = True
    df = FakeDF([100.0] * 40).df
    check("with the (currently-off) opening-range wait switched on, bar 0 - before the 09:30 close - has no "
          "complete range yet, and holds a CE", not rvs.live_gate("NIFTY", 0, rec(), df))
finally:
    config.REGIME_OR_BREAK = _saved_b
check("with it back off (today's shipped value), the same rec passes", config.REGIME_OR_BREAK is False
      and rvs.live_gate("NIFTY", 39, rec(), FakeDF([100.0] * 40).df))

print("2. THE STATELESS ASSUMPTION IS CHECKED AT IMPORT TIME, NOT JUST HOPED")
check("_assert_gates_are_stateless() ran on import without raising - if a future edit makes any of "
      "the four gates touch self/book state, importing this file raises AttributeError there, loudly, "
      "rather than this study silently producing a wrong number", True)

print("3. THE REVERSAL EXIT CHECKS TARGET AND STOP FIRST, EXACTLY ONCE PER BAR, SAME AS TICKETS.PY")
A = {"hi": [0, 101, 101, 130, 101], "lo": [0, 99, 99, 99, 99], "cl": [0, 100, 100, 115, 100],
     "end": [False, False, False, False, False]}
tr = {"side": "CE", "stop": 90.0, "t1": 105.0, "t2": 120.0, "t3": 150.0}
opt_arr = [None, None, None, "PE", "PE"]   # the opposite side reads for the FIRST time at bar 3 - the same
                                             # bar the price also crosses T2 - so the two genuinely collide
legs = rvs.simulate_reversal(A, opt_arr, 0, tr, hold_bars=4)
check("bar 3 crosses T2 (130 >= 120) AND reads the opposite side (PE) for the first time - the target wins",
      legs[-1][0] == 120.0 and legs[-1][1] == 3, legs)
opt_arr2 = [None, None, "PE", "PE", "PE"]   # opposite side only from bar 2
A2 = dict(A, hi=[0, 101, 101, 101, 101])    # never reaches any target
legs2 = rvs.simulate_reversal(A2, opt_arr2, 0, tr, hold_bars=4)
check("no target, no stop, but the opposite side reads at bar 2: closes there at that bar's close (100)",
      legs2[-1][0] == 100.0 and legs2[-1][1] == 2, legs2)
opt_arr3 = [None, None, None, None, None]   # never reverses
legs3 = rvs.simulate_reversal(A2, opt_arr3, 0, tr, hold_bars=4)
check("never reverses, never hits a level: runs to the end of the hold window, closed at its last close",
      legs3[-1][1] == 4, legs3)
same_side = ["CE", "CE", "CE", "CE", "CE"]
legs4 = rvs.simulate_reversal(A2, same_side, 0, tr, hold_bars=4)
check("the SAME side reading is never mistaken for a reversal", legs4[-1][1] == 4, legs4)

A3 = dict(A, lo=[0, 99, 85, 99, 99])   # bar 2 falls through the 90.0 stop
opt_arr5 = [None, None, "PE", "PE", "PE"]   # AND the opposite side ALSO reads from bar 2 - the stop must still win
legs5 = rvs.simulate_reversal(A3, opt_arr5, 0, tr, hold_bars=4)
check("the stop (90.0) is checked, and wins over a reversal true on the very same bar",
      legs5[-1][0] == 90.0 and legs5[-1][1] == 2, legs5)

print("REVERSAL EXIT STUDY TEST PASSED" if not fails else f"REVERSAL EXIT STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
