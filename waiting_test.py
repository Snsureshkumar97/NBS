#!/usr/bin/env python3
"""
waiting_test.py — the ticket must say WHICH rule is holding it

The screen used to print "A ticket is issued when the direction changes" no
matter why the entry was held. That sentence is true for exactly one of the
five gates, and it is the least common one: a 100%-confidence bearish signal
sitting out a 20-minute cooldown read as though the tool disagreed with a
screen full of agreement.
"""
import sys
import time

sys.path.insert(0, "/tmp/tkstub")
sys.path.insert(0, "/home/claude/trading-tool")

import datetime as dt

import tkinter as tk
import config
import gui

# The clock is pinned to mid-session. Left to the wall clock, every check here
# passes or fails depending on what time of day the suite is run — the 09:20
# opening block would hold every ticket before 09:20 IST and nothing after.
gui.now_ist = lambda: dt.datetime(2026, 9, 8, 11, 42, 5)

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(label)


REC = {"index": "SENSEX", "bias": "BEARISH", "option_type": "PE",
       "suggested_strike": 76300, "spot": 76296.09,
       "index_targets": [76100.0, 76000.0, 75900.0], "index_stop_loss": 76450.0,
       "premium_targets": [309.88, 353.78, 397.67], "premium_stop_loss": 101.29,
       "live_ltp": 251.35, "premium_source": "live", "lot_size": 20,
       "option_chain": {"available": True, "pcr": 0.76, "max_pain": 76400,
                        "strikes": [
           {"strike": 76300, "call_ltp": 100.0, "put_ltp": 251.35,
            "call_oi": 1, "put_oi": 1}]},
       "technical": {"last_close": 76296.09, "last_rsi": 31.0, "adx": 30.2,
                     "adx_ok": True, "vwap": 76311.6, "vwap_gap": -15.5,
                     "macd_hist": -68.71, "ema_fast": 76340.0,
                     "ema_slow": 76400.0, "trend_score": -1, "macd_score": -1,
                     "rsi_score": -1, "vwap_score": -1, "total_score": -4,
                     "max_score": 4, "last_atr": 90.0,
                     "last_swing_low": 76135.72, "last_swing_high": 76521.67}}


def app_with_signal():
    root = tk.Tk()
    a = gui.SignalApp(root)
    a.popup_var.set(False)
    root.bell = lambda: None
    a._focus = a.current = "SENSEX"
    return a


def reason(a):
    a._consider_signal(REC, confirm_now=True)
    return getattr(a, "_wait_reason", None)


print("\n0. WITH THE GAP AT 0, NOTHING IS HELD BY THE CLOCK")
_was = config.MIN_MINUTES_BETWEEN_TICKETS
check("the shipped default is 0 — the hold was removed by request",
      _was == 0, str(_was))
a = app_with_signal()
a._last_ticket_any = time.time() - 5          # a ticket five seconds ago
ok = a._consider_signal(REC, confirm_now=True)
check("a ticket five seconds old does not block the next one", ok is True,
      str(a._wait_reason))

print("\n1. WHEN A GAP *IS* SET, IT IS NAMED AS ITSELF")
config.MIN_MINUTES_BETWEEN_TICKETS = 20
a = app_with_signal()
a._last_ticket_any = time.time() - 5 * 60      # a ticket 5 minutes ago
r = reason(a)
check("held by the ticket gap", r and r[0] == "ticket_gap", str(r))
check("it says how much longer", r and "more minute" in r[2], str(r))
check("it says the gap is shared across indices",
      r and "across all indices" in r[2], str(r))
print(f"       \"{r[2]}\"")

config.MIN_MINUTES_BETWEEN_TICKETS = _was

print("\n2. AN OPEN POSITION ON THIS INDEX IS NAMED AS ITSELF")
a = app_with_signal()
a.active_trade = {"status": "OPEN", "index": "SENSEX", "option_type": "PE",
                  "strike": 76300, "use_premium": True, "entry_ltp": 251.0,
                  "lot_size": 20}
r = reason(a)
check("held by the open position", r and r[0] == "position_open", str(r))
print(f"       \"{r[2]}\"")

print("\n3. THE SAME-DIRECTION RULE IS NAMED — AND ONLY IT MENTIONS DIRECTION")
a = app_with_signal()
a.reentry_var.set(False)
a.last_bias_signature = ("BEARISH", "PE")
r = reason(a)
check("held by the direction signature", r and r[0] == "same_direction", str(r))
check("this is the ONLY reason that talks about the direction changing",
      "direction to change" in r[2], str(r))
print(f"       \"{r[2]}\"")

print("\n4. WITH RE-ENTRY ON, THE SAME STATE IS A COOLDOWN, NOT A LOCKOUT")
a = app_with_signal()
a.reentry_var.set(True)
a.last_bias_signature = ("BEARISH", "PE")
a._last_ticket_at = gui.now_ist()
r = reason(a)
check("held by the re-entry cooldown", r and r[0] == "reentry_cooldown", str(r))
check("it does NOT claim the direction must change",
      "direction to change" not in r[2], str(r))
print(f"       \"{r[2]}\"")

print("\n5. THE CONFIRMATION TIMER IS NAMED, WITH THE TIME LEFT")
a = app_with_signal()
a._consider_signal(REC)          # starts the clock, does not confirm
r = getattr(a, "_wait_reason", None)
check("held while confirming", r and r[0] == "confirming", str(r))
print(f"       \"{r[2]}\"")

print("\n6. THE SCREEN PRINTS THE REASON, NOT THE OLD ASSERTION")
config.MIN_MINUTES_BETWEEN_TICKETS = 20
a = app_with_signal()
a._last_ticket_any = time.time() - 60
a._consider_signal(REC, confirm_now=True)
config.MIN_MINUTES_BETWEEN_TICKETS = _was
a.last_rec = REC
a._render_trade_tracker(current_price=None)
header = a.trade_header_label.text or ""
badge = a.badge_label.text or ""
print(f"       badge: {badge.strip()}")
print(f"       {header.splitlines()[-1]}")
check("the badge no longer says PREVIEW", "PREVIEW" not in badge, badge)
check("the header does not claim the direction must change",
      "direction changes" not in header, header)
check("the header names the real rule", "minutes between tickets" in header,
      header)

print("\n7. THE DAILY BRAKE EXPLAINS ITSELF IN THE SAME WORDS")
import config as _c
_was = _c.DAILY_LIMITS_ON, _c.MAX_TRADES_PER_DAY
try:
    a = app_with_signal()
    a.limits_var.set(True)
    a._day_cache = (time.time(), (9, 0, 0))     # nine tickets already today
    r = reason(a)
    check("the daily brake is reported like every other rule",
          r is not None and len(r) == 3, str(r))
    check("it is NOT reported as a direction problem",
          r and r[0] not in ("same_direction",), str(r))
    print(f"       \"{r[2]}\"")
finally:
    _c.DAILY_LIMITS_ON, _c.MAX_TRADES_PER_DAY = _was

print("\n8. A CLEAN SIGNAL STILL ISSUES A TICKET AND CLEARS THE REASON")
a = app_with_signal()
ok = a._consider_signal(REC, confirm_now=True)
check("the ticket was issued", ok is True)
check("no stale reason left behind", a._wait_reason is None,
      str(a._wait_reason))

print("\n9. THE GAP CAN BE COUNTED PER INDEX INSTEAD")
old = getattr(config, "TICKET_GAP_PER_INDEX", False)
try:
    config.TICKET_GAP_PER_INDEX = True
    config.MIN_MINUTES_BETWEEN_TICKETS = 20
    a = app_with_signal()
    a._last_ticket_any = time.time() - 60       # another index traded a minute ago
    ok = a._consider_signal(REC, confirm_now=True)
    check("another index's ticket no longer blocks this one", ok is True,
          str(a._wait_reason))

    a = app_with_signal()
    a._last_ticket_at = gui.now_ist()           # THIS index traded just now
    a.last_bias_signature = None
    r = reason(a)
    check("this index's own ticket still blocks it",
          r and r[0] == "ticket_gap", str(r))
    check("and it says so", r and "on this index" in r[2], str(r))
finally:
    config.TICKET_GAP_PER_INDEX = old
    config.MIN_MINUTES_BETWEEN_TICKETS = _was

print()
if fails:
    print(f"WAITING TEST FAILED — {len(fails)}: " + "; ".join(fails))
    raise SystemExit(1)
print("WAITING TEST PASSED")
