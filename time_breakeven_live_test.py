#!/usr/bin/env python3
"""The time-based breakeven in tickets.py's TicketBook._check_price() - config.TIME_BREAKEVEN_
MINUTES. If T1 has not been touched within that many minutes of entry, the stop tightens to
breakeven and never loosens again; it does not close the trade by itself. Lives in the same
_check_price() the staircase trailing stop (trailing_stop_test.py) does, so both rule tickets
and AI tickets get it (ai_desk.py's self.book = tickets.TicketBook(...)) - not retested here,
this file only exercises the new mechanic. Paper/internal only - nothing here is a real broker
order; the resting Zerodha/Delta stop stays exactly where it was placed (live_orders.py,
delta_orders.py - untouched by this)."""
import datetime as dt
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import tickets

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_now = {"t": dt.datetime(2026, 9, 29, 10, 0, 0, tzinfo=IST)}
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
    return tickets.TicketBook(owner=None, market="nse_index", path=os.path.join(d, "t.csv"))


def mk(option_type="CE", use_premium=True, entry=90.0, targets=(105.0, 114.0, 120.0),
       stop=70.0, exit_at="T3", index_entry=25000.0):
    return {"index": "NIFTY", "option_type": option_type, "strike": 25000,
            "entry_time": "10:00:00", "entry_spot": index_entry,
            "entry_ts": tickets.now_ist(),
            "entry_ltp": entry if use_premium else None,
            "use_premium": use_premium, "lot_size": 65, "lots": 1, "exit_at": exit_at,
            "index_targets": [] if use_premium else list(targets),
            "index_sl": None if use_premium else stop,
            "premium_targets": list(targets) if use_premium else [],
            "premium_sl": stop if use_premium else None,
            "hit": {"T1": False, "T2": False, "T3": False},
            "hit_time": {"T1": None, "T2": None, "T3": None},
            "sl_hit": False, "sl_hit_time": None, "time_breakeven_done": False,
            "status": "OPEN", "trade_id": "NIFTY-tbtest"}


was_minutes = config.TIME_BREAKEVEN_MINUTES

print("1. BEFORE THE WAIT ELAPSES, NOTHING CHANGES - THE ORIGINAL STOP STILL APPLIES")
config.TIME_BREAKEVEN_MINUTES = 60
b = book()
b.books["NIFTY"].trade = mk()
advance(59)
b.tick_price("NIFTY", 95.0)     # well short of T1 (105), well short of the original stop (70)
t = b.books["NIFTY"].trade
check("59 of 60 minutes in: the stop is untouched", t["premium_sl"] == 70.0, t["premium_sl"])
check("not marked done yet", not t["time_breakeven_done"])

print("2. AT/AFTER THE WAIT, WITH T1 STILL UNTOUCHED, THE STOP TIGHTENS TO BREAKEVEN (ENTRY)")
advance(2)      # now 61 minutes since entry, past the 60-minute wait
b.tick_price("NIFTY", 95.0)     # same price - the tighten fires on time alone, not on price
t = b.books["NIFTY"].trade
check("the stop tightens to the entry premium (90.0), not the original 70.0",
      t["premium_sl"] == 90.0, t["premium_sl"])
check("marked done so it is not recomputed every tick", t["time_breakeven_done"] is True)
check("still open - this only tightens the stop, it does not close the trade",
      t["status"] == "OPEN", t["status"])

print("3. ...AND THAT TIGHTENED STOP ACTUALLY CLOSES A TRADE THAT GIVES BACK TO IT")
evs = b.tick_price("NIFTY", 90.0)     # drifts back down to the new (tightened) stop
t = b.books["NIFTY"].trade
check("closed at the tightened breakeven stop, not the original 70.0",
      "stop-loss hit" in t["status"], t["status"])
check("closed at the observed price", evs and evs[-1]["trade"]["exit"] == 90.0, evs)
check("pnl at breakeven is ~0 (before lot size), not the -20 the original stop would have cost",
      evs[-1]["trade"]["pnl"] == 0.0, evs[-1]["trade"]["pnl"])

print("4. ONCE T1 IS TOUCHED, THE TIMER NEVER OVERRIDES THE (BETTER) T1 RATCHET")
b = book()
b.books["NIFTY"].trade = mk()
b.tick_price("NIFTY", 108.0)     # past T1 (105) well within the wait
t = b.books["NIFTY"].trade
check("T1 ratchets the stop to 105 as usual", t["premium_sl"] == 105.0)
advance(61)                       # now well past the 60-minute wait
b.tick_price("NIFTY", 108.0)
t = b.books["NIFTY"].trade
check("the stop stays at T1 (105) - the timer does not touch a trade that already worked",
      t["premium_sl"] == 105.0, t["premium_sl"])
# time_breakeven_done itself stays False here - the block that sets it is never entered once
# hit["T1"] is True, since that alone permanently guards it. hit["T1"] never resets on an open
# trade, so there is no path back to breakeven regardless of what time_breakeven_done holds.
check("hit['T1'] alone is enough to permanently block the timer, whatever time_breakeven_done is",
      t["hit"]["T1"] is True and not t["time_breakeven_done"])

print("5. THE MIRROR FOR A PE (stop tightens DOWN to breakeven, not up)")
b = book()
b.books["NIFTY"].trade = mk(option_type="PE", use_premium=False,
                            targets=(24950.0, 24920.0, 24900.0), stop=25100.0, index_entry=25000.0)
advance(61)
b.tick_price("NIFTY", 25000.0)   # flat at entry, T1 (24950) never touched
t = b.books["NIFTY"].trade
check("a PE's stop tightens DOWN to the entry index level (25000), not the original 25100",
      t["index_sl"] == 25000.0, t["index_sl"])

print("6. TIME_BREAKEVEN_MINUTES = 0 DISABLES IT ENTIRELY")
config.TIME_BREAKEVEN_MINUTES = 0
b = book()
b.books["NIFTY"].trade = mk()
advance(1000)     # absurdly long - if the flag were ignored, this would fire
b.tick_price("NIFTY", 95.0)
t = b.books["NIFTY"].trade
check("stop is still the original - the flag being 0 turns the whole mechanic off",
      t["premium_sl"] == 70.0, t["premium_sl"])

config.TIME_BREAKEVEN_MINUTES = was_minutes

print()
if fails:
    print(f"TIME BREAKEVEN LIVE TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("TIME BREAKEVEN LIVE TEST PASSED")
