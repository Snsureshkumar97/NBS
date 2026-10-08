#!/usr/bin/env python3
"""The stop stepping up near a target (config.NEAR_TARGET_STEPS - the user, 8 Oct 2026: "after it getting close to t2
make stop loss as t1" and "when it get close to t3 make stop loss at t2 if it reach t3 it will exit if not it will touch
the t2 stop loss in profit"), in tickets.py's real _check_price(): on a Trend Rider ticket, 90% of the way to T2 the stop
moves to T1 and 90% of the way to T3 to T2 - each once, only ever tightening, a call or a put, tracked on its premium or
the index, and the live order told to move its stop. The tool's own rules are not touched (their stop moves to T1 AT T1).

And the bug found on the way: the 2-hour breakeven and the Supertrend trail asked only "CE or PE" which way is tighter,
so on a premium-tracked PUT (whose stop sits BELOW its premium, like a call's) the trail moved the stop DOWN from T1 and
the breakeven never applied. Both now tighten the right way."""
import datetime as dt
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import tickets

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_now = {"t": dt.datetime(2026, 10, 8, 10, 0, 0, tzinfo=IST)}
tickets.now_ist = lambda: _now["t"]

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def book():
    b = tickets.TicketBook(owner=None, market="nse_index", path=os.path.join(tempfile.mkdtemp(), "t.csv"))
    b.seen = []
    b.listeners.append(lambda kind, trade: b.seen.append(kind))
    return b


def mk(system="trend_rider", option_type="CE", use_premium=True, entry=90.0, targets=(100.0, 110.0, 120.0), stop=70.0,
       exit_at="T3", index_entry=25000.0, plain=True):
    return {"index": "NIFTY", "option_type": option_type, "strike": 25000, "entry_time": "10:00:00",
            "entry_spot": index_entry, "entry_ts": tickets.now_ist(), "entry_ltp": entry if use_premium else None,
            "use_premium": use_premium, "lot_size": 65, "lots": 1, "exit_at": exit_at, "system": system,
            "plain_exit": plain, "rule_strategy": plain,
            "index_targets": [] if use_premium else list(targets), "index_sl": None if use_premium else stop,
            "premium_targets": list(targets) if use_premium else [], "premium_sl": stop if use_premium else None,
            "hit": {"T1": False, "T2": False, "T3": False}, "hit_time": {"T1": None, "T2": None, "T3": None},
            "sl_hit": False, "sl_hit_time": None, "time_breakeven_done": False, "status": "OPEN", "trade_id": "NIFTY-nts"}


check("the setting: near T2 -> T1, near T3 -> T2, at 90% of the way, for the Trend Rider only",
      config.NEAR_TARGET_STEPS == {"trend_rider": [(0.9, "T2", "T1"), (0.9, "T3", "T2")]}, config.NEAR_TARGET_STEPS)

print("1. A TREND RIDER CALL, TRACKED ON ITS PREMIUM: entry 90, T1 100, T2 110, T3 120, stop 70")
b = book()
bk = b.books["NIFTY"]
bk.trade = mk()
b.tick_price("NIFTY", 107.9)                     # short of 90% of the way to T2 (108)
check("short of 90% to T2: the stop stays at 70", bk.trade["premium_sl"] == 70.0, bk.trade["premium_sl"])
b.tick_price("NIFTY", 108.0)
check("90% of the way to T2: the stop moves to T1 (100)", bk.trade["premium_sl"] == 100.0, bk.trade["premium_sl"])
check("...and the live order is told to move its stop", "trailed" in b.seen, b.seen)
b.seen.clear()
b.tick_price("NIFTY", 109.0)
check("...once: a second tick there changes nothing", bk.trade["premium_sl"] == 100.0 and "trailed" not in b.seen)
b.tick_price("NIFTY", 117.0)                     # 90% of the way to T3 (90 + 0.9 x 30)
check("90% of the way to T3: the stop moves to T2 (110)", bk.trade["premium_sl"] == 110.0, bk.trade["premium_sl"])
b.tick_price("NIFTY", 109.5)
check("the turn back closes it at the T2 stop, in profit", bk.trade is None or bk.trade["status"] != "OPEN",
      (bk.trade or {}).get("status"))

print("2. THE SAME FOR A PUT TRACKED ON ITS PREMIUM - its stop also sits below the premium: it moves UP")
b = book()
bk = b.books["NIFTY"]
bk.trade = mk(option_type="PE")
b.tick_price("NIFTY", 108.0)
check("a premium-tracked put: the stop moves up to T1 (100), not down", bk.trade["premium_sl"] == 100.0, bk.trade["premium_sl"])
b.tick_price("NIFTY", 117.0)
check("...and up to T2 (110)", bk.trade["premium_sl"] == 110.0, bk.trade["premium_sl"])

print("3. A PUT TRACKED ON THE INDEX: entry 25,000, T1 24,900, T2 24,800, T3 24,700, stop 25,100")
b = book()
bk = b.books["NIFTY"]
bk.trade = mk(option_type="PE", use_premium=False, targets=(24900.0, 24800.0, 24700.0), stop=25100.0)
b.tick_price("NIFTY", 24822.0)
check("short of 90% to T2 (24,820): the stop stays", bk.trade["index_sl"] == 25100.0, bk.trade["index_sl"])
b.tick_price("NIFTY", 24820.0)
check("90% to T2: the stop moves DOWN to T1 (24,900) - tighter for a put on the index", bk.trade["index_sl"] == 24900.0,
      bk.trade["index_sl"])
b.tick_price("NIFTY", 24730.0)
check("90% to T3 (24,730): to T2 (24,800)", bk.trade["index_sl"] == 24800.0, bk.trade["index_sl"])

print("4. NEVER LOOSER, AND NOT FOR THE TOOL'S OWN RULES")
b = book()
bk = b.books["NIFTY"]
bk.trade = mk(stop=104.0)                        # already above T1
b.tick_price("NIFTY", 108.0)
check("a stop already tighter than T1 is left where it is", bk.trade["premium_sl"] == 104.0, bk.trade["premium_sl"])
b = book()
bk = b.books["NIFTY"]
bk.trade = mk(system="rules", plain=False, targets=(115.0, 130.0, 150.0), exit_at="T2")
b.tick_price("NIFTY", 112.0)                     # 90% of the way to T2 (127) is far; T1 (115) not reached
check("a rules ticket: no near-target step (its stop moves AT T1, as before)", bk.trade["premium_sl"] == 70.0
      and not bk.trade.get("near_steps"), (bk.trade["premium_sl"], bk.trade.get("near_steps")))
b.tick_price("NIFTY", 116.0)
check("...and T1 itself still ratchets it to T1", bk.trade["premium_sl"] == 115.0, bk.trade["premium_sl"])

print("5. THE BUG: A PREMIUM-TRACKED PUT'S SUPERTREND TRAIL AND BREAKEVEN")
was = config.TRAIL_AFTER_T1_SUPERTREND
config.TRAIL_AFTER_T1_SUPERTREND = True
b = book()
bk = b.books["NIFTY"]
bk.trade = mk(system="rules", plain=False, option_type="PE", targets=(105.0, 114.0, 120.0), exit_at="T3")
# the Supertrend at 24,980 maps to a premium of 90 - 0.5 x (24,980 - 25,000) = 100: BELOW T1 (105)
bk.last_rec = {"supertrend": 24980.0}
b.tick_price("NIFTY", 106.0)                     # T1 touched: the stop to 105
check("after T1 the trail never pulls a put's premium stop back down below T1", bk.trade["premium_sl"] == 105.0,
      bk.trade["premium_sl"])
bk.last_rec = {"supertrend": 24960.0}            # maps to 110: tighter than T1 - followed
b.tick_price("NIFTY", 112.0)
check("...while a tighter Supertrend (110) is followed up", bk.trade["premium_sl"] == 110.0, bk.trade["premium_sl"])
config.TRAIL_AFTER_T1_SUPERTREND = was
b = book()
bk = b.books["NIFTY"]
bk.trade = mk(system="rules", plain=False, option_type="PE", targets=(105.0, 114.0, 120.0), exit_at="T2")
_now["t"] = _now["t"] + dt.timedelta(minutes=config.time_breakeven_minutes("NIFTY") + 1)
b.tick_price("NIFTY", 95.0)                      # above entry, short of T1, past the breakeven wait
check("a premium-tracked put's 2-hour breakeven now applies: the stop to the entry (90)", bk.trade["premium_sl"] == 90.0,
      bk.trade["premium_sl"])

print()
print("NEAR TARGET STEPS TEST PASSED" if not fails else f"NEAR TARGET STEPS TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
