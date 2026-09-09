#!/usr/bin/env python3
"""
exit_target_test.py — EXIT_AT_TARGET actually decides where a trade gets out

The three tiers were never partial exits: the tool holds the whole position and
closes on one of them. So this setting IS the single target, and the checks
below are about the two ways that could go wrong — the trade not closing at the
level it was told to, and everything downstream still looking for the literal
string "T3".
"""
import datetime as dt
import sys

sys.path.insert(0, "/tmp/tkstub")
sys.path.insert(0, "/home/claude/trading-tool")

import tkinter as tk
import config
import gui
import trade_log

gui.now_ist = lambda: dt.datetime(2026, 9, 8, 11, 42, 5)

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(label)


def trade(exit_at=None):
    t = {"index": "NIFTY", "option_type": "CE", "strike": 24500,
         "entry_time": "10:00:00", "entry_spot": 24480.0, "entry_ltp": 100.0,
         "use_premium": True, "lot_size": 75, "lots": 1,
         "index_targets": [24548., 24612., 24690.], "index_sl": 24402.0,
         "premium_targets": [120.0, 140.0, 175.0], "premium_sl": 75.0,
         "hit": {"T1": False, "T2": False, "T3": False},
         "hit_time": {"T1": None, "T2": None, "T3": None},
         "sl_hit": False, "sl_hit_time": None, "status": "OPEN"}
    if exit_at:
        t["exit_at"] = exit_at
    return t


def app():
    root = tk.Tk()
    a = gui.SignalApp(root)
    a.popup_var.set(False)
    root.bell = lambda: None
    return a


print("\n1. EXITING AT T1 CLOSES AT +20%, NOT AT +75%")
a = app()
a.active_trade = trade("T1")
a._check_trade_price(current_price=121.0)          # past T1 (120), not T2
check("the trade is closed", a.active_trade["status"] != "OPEN",
      a.active_trade["status"])
check("it closed on T1", "T1 hit" in a.active_trade["status"],
      a.active_trade["status"])

print("\n2. THE SAME PRICE LEAVES A T3 TICKET RUNNING")
a = app()
a.active_trade = trade("T3")
a._check_trade_price(current_price=121.0)
check("still open at +21% when the exit is +75%",
      a.active_trade["status"] == "OPEN", a.active_trade["status"])
a._check_trade_price(current_price=176.0)
check("and closes when +75% prints", "T3 hit" in a.active_trade["status"],
      a.active_trade["status"])

print("\n3. THE STOP STILL WINS, WHATEVER THE EXIT IS SET TO")
a = app()
a.active_trade = trade("T1")
a._check_trade_price(current_price=70.0)
check("stopped out", "stop-loss" in a.active_trade["status"],
      a.active_trade["status"])

print("\n4. A T1 EXIT COUNTS AS A WIN EVERYWHERE, NOT JUST WHERE IT SAYS 'T3'")
for st in ("CLOSED — T1 hit (full target reached)",
           "CLOSED — T2 hit (full target reached)",
           "CLOSED — T3 hit (full target reached)"):
    check(f"win: {st.split('—')[1].strip()}", trade_log.is_target_close(st))
for st in ("CLOSED — stop-loss hit",
           "CLOSED — signal changed before target/SL was hit",
           "CLOSED — market closed with the trade still open"):
    check(f"not a win: {st.split('—')[1].strip()[:28]}",
          not trade_log.is_target_close(st))

print("\n5. THE SETTING IS FROZEN INTO THE TICKET AT ENTRY")
old = config.EXIT_AT_TARGET
try:
    config.EXIT_AT_TARGET = "T1"
    a = app()
    REC = {"index": "NIFTY", "bias": "BULLISH", "option_type": "CE",
           "suggested_strike": 24500, "spot": 24480.0,
           "index_targets": [24548., 24612., 24690.], "index_stop_loss": 24402.,
           "premium_targets": [120., 140., 175.], "premium_stop_loss": 75.,
           "premium_source": "live", "live_ltp": 100.0, "candles": None,
           "option_chain": {"available": True, "pcr": 0.9, "max_pain": 24500,
                            "strikes": [{"strike": 24500, "call_ltp": 100.0,
                                         "put_ltp": 90.0, "call_oi": 1,
                                         "put_oi": 1}]},
           "technical": {"last_close": 24480.0, "last_rsi": 62.0, "adx": 27.0,
                         "adx_ok": True, "vwap": 24460.0, "vwap_gap": 20.0,
                         "macd_hist": 12.0, "ema_fast": 24470.0,
                         "ema_slow": 24440.0, "trend_score": 1,
                         "macd_score": 1, "rsi_score": 1, "vwap_score": 1,
                         "total_score": 4, "max_score": 4, "last_atr": 60.0,
                         "last_swing_low": 24402.0,
                         "last_swing_high": 24690.0},
           "stop_basis": "swing", "target_basis": "atr"}
    a._lock_new_trade(REC)
    check("the ticket carries the exit it was born with",
          a.active_trade.get("exit_at") == "T1", str(a.active_trade.get("exit_at")))

    # Changing the setting mid-session must not move a running trade's exit.
    config.EXIT_AT_TARGET = "T3"
    a._check_trade_price(current_price=121.0)
    check("changing the setting at lunchtime does not move a live trade's exit",
          "T1 hit" in a.active_trade["status"], a.active_trade["status"])
finally:
    config.EXIT_AT_TARGET = old

print("\n6. A NONSENSE SETTING FALLS BACK TO T3 RATHER THAN NEVER CLOSING")
a = app()
a.active_trade = trade("T9")
a._check_trade_price(current_price=176.0)
check("closed at T3", "T3 hit" in a.active_trade["status"],
      a.active_trade["status"])

print()
if fails:
    print(f"EXIT TARGET TEST FAILED — {len(fails)}: " + "; ".join(fails))
    raise SystemExit(1)
print("EXIT TARGET TEST PASSED")
