#!/usr/bin/env python3
"""WAIVE_COOLDOWN_ON_TRENDING_TARGET, wired into tickets.py's real gates -
TicketBook._same_direction_hold() and ._close(). Backtested
(conditional_cooldown_study.py): KEEP, robust across every ADX threshold
swept. This file exercises the LIVE wiring only - the backtest's own
verdict is not re-derived here, matching how time_breakeven_live_test.py
exercises _check_price()'s mechanic without re-proving
time_breakeven_study.py's own backtest result.

Shares _same_direction_hold()/_close() with the AI desk's own book
(ai_desk.py's self.book = tickets.TicketBook(...)), so both rule tickets
and AI tickets get this automatically - not separately retested here.
Paper/internal only - nothing here is a real broker order.
"""
import datetime as dt
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import tickets

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_now = {"t": dt.datetime(2026, 10, 2, 10, 0, 0, tzinfo=IST)}
tickets.now_ist = lambda: _now["t"]
def advance(minutes):
    _now["t"] = _now["t"] + dt.timedelta(minutes=minutes)

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def book():
    d = tempfile.mkdtemp()
    b = tickets.TicketBook(owner=None, market="nse_index", path=os.path.join(d, "t.csv"))
    b.reentry = True        # ALLOW_SAME_DIRECTION_REENTRY, on for every check below
    return b


def rec(adx=None):
    """Passes the room check (REENTRY_MIN_RR) on its own, so a None result
    below means the COOLDOWN step itself let it through, not a lucky room
    reading masking a real block."""
    return {"reach_to_risk": 5.0, "reach_points": 500.0, "risk_points": 100.0, "adx": adx}


def close_ticket(b, name, reason, adx):
    """Closes a ticket on this index RIGHT NOW (at the current mocked clock),
    for the given reason/ADX - via the REAL _close() path (a fake trade dict
    + a fake rec), not by poking book.last_close_reason/last_close_adx
    directly, so this also proves _close() itself wires the new fields
    correctly. Callers advance() the clock afterward to control how long ago
    this close reads as by the time _same_direction_hold is checked."""
    ib = b.books[name]
    ib.last_ticket_at = tickets.now_ist() - dt.timedelta(minutes=5)   # entry, 5 min before this close
    status = {"target": "CLOSED — T3 hit (full target reached)",
              "stop": "CLOSED — stop-loss hit",
              "other": "CLOSED — market closed with the trade still open"}[reason]
    trade = {"status": status, "use_premium": False, "entry_ltp": None,
             "entry_spot": 100.0, "lot_size": 65, "lots": 1,
             "entry_time": "09:30:00", "index": name, "strike": 25000,
             "option_type": "CE", "expiry": "2026-10-02", "trade_id": f"{name}-cctest",
             "hit": {"T1": False, "T2": False, "T3": reason == "target"},
             "sl_hit": reason == "stop"}
    b._close(ib, trade, 100.0, rec(adx=adx))
    return ib


was_waive = config.WAIVE_COOLDOWN_ON_TRENDING_TARGET
was_thresh = config.ADX_TREND_THRESHOLD
config.ADX_TREND_THRESHOLD = 20

print("1. BASELINE (feature ON): A STOP-OUT STILL GETS THE FULL COOLDOWN, EVEN WITH HIGH ADX")
config.WAIVE_COOLDOWN_ON_TRENDING_TARGET = True
b = book()
ib = close_ticket(b, "NIFTY", "stop", adx=35.0)
advance(10)     # 10 minutes since close - still inside the 20-minute cooldown
check("closed via a stop reads back as 'other', not 'target'", ib.last_close_reason == "other",
      ib.last_close_reason)
hold = b._same_direction_hold(ib, rec(adx=35.0), ("BULLISH", "CE"))
check("blocked - a stop-out earns no waiver no matter how high ADX is",
      hold is not None and hold[0] == "reentry_cooldown", hold)
check("no trend waiver was recorded", ib.trend_waiver_applied is False)

print("2. TARGET HIT WITH ADX AT/ABOVE THE THRESHOLD: THE COOLDOWN IS WAIVED")
b = book()
ib = close_ticket(b, "NIFTY", "target", adx=25.0)
advance(10)
check("closed via target reads back correctly", ib.last_close_reason == "target", ib.last_close_reason)
check("the ADX at that close is remembered", ib.last_close_adx == 25.0, ib.last_close_adx)
hold = b._same_direction_hold(ib, rec(adx=25.0), ("BULLISH", "CE"))
check("allowed - only 10 minutes since close, well inside the 20-minute cooldown, "
      "but the trend waiver lets it through", hold is None, hold)
check("the trend waiver is what fired, not the hand-chosen one",
      ib.trend_waiver_applied is True and ib.waiver_applied is False, (ib.trend_waiver_applied, ib.waiver_applied))

print("3. TARGET HIT, BUT ADX HAD ALREADY FADED BELOW THE THRESHOLD: NO WAIVER")
b = book()
ib = close_ticket(b, "NIFTY", "target", adx=15.0)
advance(10)
hold = b._same_direction_hold(ib, rec(adx=15.0), ("BULLISH", "CE"))
check("blocked - target hit, but ADX(15) was already under the threshold(20) at that close",
      hold is not None and hold[0] == "reentry_cooldown", hold)
check("no trend waiver was recorded", ib.trend_waiver_applied is False)

print("4. A TIME/SQUARE-OFF CLOSE ('other') EARNS NO WAIVER, EVEN WITH HIGH ADX")
b = book()
ib = close_ticket(b, "NIFTY", "other", adx=35.0)
advance(10)
hold = b._same_direction_hold(ib, rec(adx=35.0), ("BULLISH", "CE"))
check("blocked - never having reached target earns no waiver regardless of ADX",
      hold is not None and hold[0] == "reentry_cooldown", hold)

print("5. PAST THE ORDINARY COOLDOWN WINDOW, IT NO LONGER MATTERS HOW THE PRIOR TRADE CLOSED")
b = book()
ib = close_ticket(b, "NIFTY", "stop", adx=5.0)
advance(25)     # 25 minutes since close - clears the 20-minute cooldown on the clock alone
hold = b._same_direction_hold(ib, rec(adx=5.0), ("BULLISH", "CE"))
check("allowed on the ordinary clock alone - 25 minutes already clears the 20-minute cooldown",
      hold is None, hold)

print("6. WAIVE_COOLDOWN_ON_TRENDING_TARGET = False DISABLES THE WHOLE MECHANIC - "
      "FALLS BACK TO EXACTLY TODAY'S BEHAVIOUR")
config.WAIVE_COOLDOWN_ON_TRENDING_TARGET = False
b = book()
ib = close_ticket(b, "NIFTY", "target", adx=35.0)
advance(10)
hold = b._same_direction_hold(ib, rec(adx=35.0), ("BULLISH", "CE"))
check("blocked - the flag being off means even a perfect trending-target close earns no waiver",
      hold is not None and hold[0] == "reentry_cooldown", hold)
config.WAIVE_COOLDOWN_ON_TRENDING_TARGET = True

print("7. THE HAND-CHOSEN SKIP (skip_cooldown) STILL WORKS INDEPENDENTLY OF THE TREND WAIVER")
b = book()
ib = close_ticket(b, "NIFTY", "stop", adx=5.0)    # would NOT earn a trend waiver
advance(10)
# skip_cooldown() only acts once a cooldown is actually pending in
# book.wait_reason - normally set by a full _consider() cycle; reproduced
# here by recording what _same_direction_hold itself just found.
ib.wait_reason = b._same_direction_hold(ib, rec(adx=5.0), ("BULLISH", "CE"))
check("the fixture's own premise: this close is genuinely still under cooldown, unaided",
      ib.wait_reason is not None and ib.wait_reason[0] == "reentry_cooldown", ib.wait_reason)
ok, msg = b.skip_cooldown("NIFTY")
check(f"skip_cooldown itself reports {'ok' if ok else 'refused'}", ok, msg)
if ok:
    hold = b._same_direction_hold(ib, rec(adx=5.0), ("BULLISH", "CE"))
    check("allowed via the hand-chosen skip, even though this close would never earn a trend waiver",
          hold is None, hold)
    check("the MANUAL waiver fired, not the trend one",
          ib.waiver_applied is True and ib.trend_waiver_applied is False,
          (ib.waiver_applied, ib.trend_waiver_applied))

print("8. THE BOUNDARY: ADX EXACTLY AT THE THRESHOLD STILL COUNTS AS A WAIVER")
b = book()
ib = close_ticket(b, "NIFTY", "target", adx=20.0)
advance(10)
hold = b._same_direction_hold(ib, rec(adx=20.0), ("BULLISH", "CE"))
check("allowed - ADX(20) == the threshold(20), >= counts as still trending", hold is None, hold)

print("9. cooldown_waived_trend ON THE OPENED TICKET ITSELF REFLECTS WHICH WAIVER LET IT THROUGH")
b = book()
ib = close_ticket(b, "NIFTY", "target", adx=25.0)
advance(10)
b._same_direction_hold(ib, rec(adx=25.0), ("BULLISH", "CE"))   # sets trend_waiver_applied as a side effect
check("trend_waiver_applied is live ahead of a real _open() carrying it onto the new ticket",
      ib.trend_waiver_applied is True)

config.WAIVE_COOLDOWN_ON_TRENDING_TARGET = was_waive
config.ADX_TREND_THRESHOLD = was_thresh

print()
if fails:
    print(f"CONDITIONAL COOLDOWN LIVE TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("CONDITIONAL COOLDOWN LIVE TEST PASSED")
