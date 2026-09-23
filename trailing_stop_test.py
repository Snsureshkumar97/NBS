#!/usr/bin/env python3
"""The staircase trailing stop in tickets.py's TicketBook._check_price().

Asked for by the user on 22 Sep 2026: "when the ltp hit target one and cross
the stop loss should change to target 1 ... if the strike have more way to go
we can do the same way." Any target strictly before the trade's own exit_at
becomes the new stop the instant it is crossed, so a reversal can only cost
back to the last rung reached, never all the way to the original stop. This
lives in the one _check_price() both rule tickets and AI tickets share (see
ai_desk.py's self.book = tickets.TicketBook(...)), so one set of tests here
covers both. Paper/internal only - nothing here is a real broker order; the
resting Zerodha/Delta stop stays exactly where it was placed (live_orders.py,
delta_orders.py - untouched by this).
"""
import datetime as dt
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tickets
import trade_log

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
tickets.now_ist = lambda: dt.datetime(2026, 9, 22, 11, 0, 0, tzinfo=IST)

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
            "entry_ltp": entry if use_premium else None,
            "use_premium": use_premium, "lot_size": 65, "lots": 1, "exit_at": exit_at,
            "index_targets": [] if use_premium else list(targets),
            "index_sl": None if use_premium else stop,
            "premium_targets": list(targets) if use_premium else [],
            "premium_sl": stop if use_premium else None,
            "hit": {"T1": False, "T2": False, "T3": False},
            "hit_time": {"T1": None, "T2": None, "T3": None},
            "sl_hit": False, "sl_hit_time": None, "status": "OPEN",
            "trade_id": "NIFTY-trailtest"}


sl_field = lambda t: "premium_sl" if t["use_premium"] else "index_sl"

print("1. CE / PREMIUM: ONE RUNG CROSSED, ONE RUNG TO TRAIL TO")
b = book()
b.books["NIFTY"].trade = mk()
b.tick_price("NIFTY", 95.0)                             # short of T1 (105)
t = b.books["NIFTY"].trade
check("short of T1: nothing trails, still open", t["status"] == "OPEN" and t["premium_sl"] == 70.0)
b.tick_price("NIFTY", 108.0)                             # past T1
t = b.books["NIFTY"].trade
check("T1 crossed: the stop ratchets up to it, no model/user action", t["status"] == "OPEN" and t["premium_sl"] == 105.0)
evs = b.tick_price("NIFTY", 100.0)                       # reversal - but only back to T1, not the original 70
t = b.books["NIFTY"].trade
check("the reversal costs back to T1, not the original stop: closed in profit",
      "trailed to T1" in t["status"] and "stop-loss hit" in t["status"], t["status"])
check("closed at the observed price, not at the rung's exact level",
      evs and evs[-1]["kind"] == "closed" and evs[-1]["trade"]["exit"] == 100.0, evs)
check("pnl reflects entry to the actual exit tick, lot size and lots",
      evs[-1]["trade"]["pnl"] == round((100.0 - 90.0) * 65 * 1, 2), evs[-1]["trade"]["pnl"])

print("2. CE / PREMIUM: A SECOND RUNG TRAILS FURTHER THAN THE FIRST")
b = book()
b.books["NIFTY"].trade = mk()
b.tick_price("NIFTY", 108.0)                             # past T1
b.tick_price("NIFTY", 116.0)                             # past T2 (114) too
t = b.books["NIFTY"].trade
check("a second rung crossed trails the stop again, past the first", t["premium_sl"] == 114.0)
b.tick_price("NIFTY", 110.0)                             # gives back T2, but the stop now reads T2's price
t = b.books["NIFTY"].trade
check("the further it ran, the further the stop had climbed: trailed to T2, not T1",
      "trailed to T2" in t["status"], t["status"])

print("3. THE EXIT RUNG ITSELF NEVER TRAILS TO ITSELF - IT CLOSES THE TRADE")
b = book()
b.books["NIFTY"].trade = mk()                            # exit_at T3
evs = b.tick_price("NIFTY", 125.0)                        # a gap straight through T1, T2 and T3 in one tick
t = b.books["NIFTY"].trade
check("hitting the exit rung closes at the full target, not a trailed stop",
      "T3 hit (full target reached)" in t["status"] and "trailed" not in t["status"], t["status"])
check("closed at the price that actually printed, not the target's own level",
      evs[-1]["trade"]["exit"] == 125.0, evs[-1]["trade"]["exit"])

print("4. ONLY RUNGS BEFORE THE EXIT ACT AS WAYPOINTS - CONFIG'S DEFAULT T2 EXIT")
b = book()
b.books["NIFTY"].trade = mk(exit_at="T2")                 # the rule tickets' own default (config.EXIT_AT_TARGET)
b.tick_price("NIFTY", 108.0)                               # past T1: T1 is before T2, so it trails
t = b.books["NIFTY"].trade
check("T1 is a waypoint before a T2 exit: it trails the stop", t["status"] == "OPEN" and t["premium_sl"] == 105.0)
evs = b.tick_price("NIFTY", 116.0)                          # past T2, the exit rung itself
t = b.books["NIFTY"].trade
check("T2 is the exit here, not a waypoint: it closes the trade outright",
      "T2 hit (full target reached)" in t["status"], t["status"])

print("5. SAME-TICK ORDERING: THE STOP THAT JUST MOVED CANNOT SELF-TRIGGER THAT SAME TICK")
b = book()
b.books["NIFTY"].trade = mk()
evs = b.tick_price("NIFTY", 105.0)                          # lands exactly on T1
t = b.books["NIFTY"].trade
check("landing exactly on T1 trails the stop to 105 but does not also read as hitting it this tick",
      t["status"] == "OPEN" and t["premium_sl"] == 105.0 and not evs[-1].get("kind") == "closed" if evs else True,
      (t["status"], t["premium_sl"]))

print("6. PE / PREMIUM: THE SAME MATH - A PREMIUM RISES TOWARD PROFIT REGARDLESS OF SIDE")
b = book()
b.books["NIFTY"].trade = mk(option_type="PE")
b.tick_price("NIFTY", 108.0)
t = b.books["NIFTY"].trade
check("a PE premium ticket trails exactly like a CE one", t["premium_sl"] == 105.0)
evs = b.tick_price("NIFTY", 100.0)
t = b.books["NIFTY"].trade
check("...and closes the same way, trailed to T1", "trailed to T1" in t["status"], t["status"])

print("7. CE / INDEX: THE INDEX ITSELF RISING TRAILS THE STOP UP")
b = book()
b.books["NIFTY"].trade = mk(use_premium=False, targets=(25050.0, 25080.0, 25100.0), stop=24900.0)
b.tick_price("NIFTY", 25060.0)                              # past T1 (25050)
t = b.books["NIFTY"].trade
check("the index crossing T1 trails index_sl up to it", t["index_sl"] == 25050.0)
evs = b.tick_price("NIFTY", 25040.0)                        # reversal - stop now at T1, not the original 24900
t = b.books["NIFTY"].trade
check("closed trailed to T1 - the index mode uses the same staircase as premium",
      "trailed to T1" in t["status"], t["status"])

print("8. PE / INDEX: DIRECTION REVERSED - A FALLING INDEX IS THE PE'S PROFIT SIDE")
b = book()
b.books["NIFTY"].trade = mk(option_type="PE", use_premium=False,
                             targets=(24950.0, 24920.0, 24900.0), stop=25100.0)
b.tick_price("NIFTY", 24940.0)                              # past T1 (24950, falling)
t = b.books["NIFTY"].trade
check("a PE's T1 trails the stop DOWN, not up - toward the entry, tightening the room to give back",
      t["index_sl"] == 24950.0, t["index_sl"])
evs = b.tick_price("NIFTY", 24960.0)                        # index rises back through the trailed stop
t = b.books["NIFTY"].trade
check("the index rising back through the trailed level closes it - trailed to T1, not the original 25100",
      "trailed to T1" in t["status"], t["status"])

print("9. A TRAILED CLOSE IS STILL A PLAIN STOP TO EVERYTHING DOWNSTREAM")
b = book()
b.books["NIFTY"].trade = mk()
b.tick_price("NIFTY", 108.0)
b.tick_price("NIFTY", 100.0)
t = b.books["NIFTY"].trade
check("not counted as a target win", not trade_log.is_target_close(t["status"]), t["status"])
check("'stop-loss hit' is still a literal substring - the compat every other reader keys on",
      "stop-loss hit" in t["status"], t["status"])

print()
if fails:
    print(f"TRAILING STOP TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("TRAILING STOP TEST PASSED")
