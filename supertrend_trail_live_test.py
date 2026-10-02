#!/usr/bin/env python3
"""The post-T1 Supertrend trail in tickets.py's TicketBook._check_price() - config.
TRAIL_AFTER_T1_SUPERTREND. Once T1 has been touched, the stop keeps following the
index's own Supertrend line (rec["supertrend"], computed in signal_engine.compute_
technical_signal() via config.supertrend_params(index_key)) instead of sitting flat
at T1 for the rest of the trade - only ever tightening further, never loosening.
Backtested in supertrend_trail_study.py (2 Oct 2026): KEEP against the real live
exit, both periods. Lives in the same _check_price() the staircase trailing stop
(trailing_stop_test.py) and the time-based breakeven (time_breakeven_live_test.py)
do, so both rule tickets and AI tickets get it - not retested here, this file only
exercises the new mechanic. Paper/internal only - nothing here is a real broker
order; the resting Zerodha/Delta stop stays exactly where it was placed
(live_orders.py, delta_orders.py - untouched by this)."""
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
            "status": "OPEN", "trade_id": "NIFTY-sttest"}


def rec(supertrend):
    return {"supertrend": supertrend}


was_flag = config.TRAIL_AFTER_T1_SUPERTREND
config.TRAIL_AFTER_T1_SUPERTREND = True

# CE targets used below: T1=25100, T2=25140, T3=25200 - a tick at 25110 touches
# T1 only (short of T2), so the staircase ratchet (trailing_stop_test.py) sets the
# stop to exactly T1 (25100) and stops there; this file is only about what the
# Supertrend trail does ON TOP of that, afterwards.

print("1. BEFORE T1 IS TOUCHED, THE SUPERTREND READING IS IGNORED - STOP STAYS ORIGINAL")
b = book()
b.books["NIFTY"].trade = mk(use_premium=False, targets=(25100.0, 25140.0, 25200.0), stop=24900.0)
b.books["NIFTY"].last_rec = rec(25050.0)      # a reading, but T1 hasn't been hit yet
b.tick_price("NIFTY", 25000.0)                # flat, short of T1 (25100)
t = b.books["NIFTY"].trade
check("stop untouched - T1 not hit yet", t["index_sl"] == 24900.0, t["index_sl"])
check("T1 not yet marked hit", not t["hit"]["T1"])

print("2. ONCE T1 IS TOUCHED, A SUPERTREND BEYOND T1 (BUT STILL BELOW PRICE) RATCHETS FURTHER")
b.books["NIFTY"].last_rec = rec(25105.0)      # beyond T1's own ratchet (25100), still below price
b.tick_price("NIFTY", 25110.0)                # touches T1 (25100) only, not T2 (25140)
t = b.books["NIFTY"].trade
check("T1 ratchet fires as usual", t["hit"]["T1"] is True)
check("T2 not touched by this tick", not t["hit"]["T2"])
check("the trail then pushes the stop past T1, to the Supertrend level (25105)",
      t["index_sl"] == 25105.0, t["index_sl"])
check("still open - this only tightens the stop", t["status"] == "OPEN", t["status"])

print("3. A WORSE (LOWER) SUPERTREND READING NEVER LOOSENS THE STOP BACK DOWN")
b.books["NIFTY"].last_rec = rec(25080.0)      # below the current stop (25105) - less favourable
b.tick_price("NIFTY", 25110.0)                # price unchanged, well clear of the stop either way
t = b.books["NIFTY"].trade
check("stop stays at 25105 - a less favourable reading is simply ignored",
      t["index_sl"] == 25105.0, t["index_sl"])

print("4. AND THAT TRAILED STOP ACTUALLY CLOSES A TRADE THAT GIVES BACK TO IT")
evs = b.tick_price("NIFTY", 25105.0)          # drifts back down onto the trailed stop
t = b.books["NIFTY"].trade
check("closed at the Supertrend-trailed stop (25105), not T1's own 25100 or the original 24900",
      "stop-loss hit" in t["status"], t["status"])
check("closed at the observed price", evs and evs[-1]["trade"]["exit"] == 25105.0, evs)

print("5. PREMIUM MODE: THE SUPERTREND INDEX LEVEL IS CONVERTED THE SAME WAY ENTRY TARGETS ARE")
b = book()
b.books["NIFTY"].trade = mk(use_premium=True, entry=90.0, targets=(105.0, 114.0, 120.0),
                             stop=70.0, index_entry=25000.0)
# entry_spot=25000, entry_ltp=90, APPROX_ATM_DELTA=0.5 (default) -> at index 25050,
# the implied premium level is 90 + 0.5*(25050-25000) = 115.0
b.books["NIFTY"].last_rec = rec(25050.0)
b.tick_price("NIFTY", 108.0)                  # premium touches T1 (105) only, not T2 (114)
t = b.books["NIFTY"].trade
check("T1 ratchet fires in premium terms as usual", t["hit"]["T1"] is True)
check("T2 not touched", not t["hit"]["T2"])
check("the trail converts 25050 index points into the implied premium (115.0) via the entry anchor",
      t["premium_sl"] == 115.0, t["premium_sl"])

print("6. PE MIRROR: THE STOP TRAILS DOWN, NOT UP, AND STILL NEVER LOOSENS")
b = book()
b.books["NIFTY"].trade = mk(option_type="PE", use_premium=False,
                             targets=(24900.0, 24860.0, 24800.0), stop=25100.0, index_entry=25000.0)
b.books["NIFTY"].last_rec = rec(24895.0)      # favourable for a PE: below T1's own ratchet (24900)
b.tick_price("NIFTY", 24890.0)                # touches T1 (24900) only, not T2 (24860)
t = b.books["NIFTY"].trade
check("T1 ratchets to 24900 as usual", t["hit"]["T1"] is True)
check("T2 not touched", not t["hit"]["T2"])
check("the trail pushes the stop further DOWN, to 24895", t["index_sl"] == 24895.0, t["index_sl"])
b.books["NIFTY"].last_rec = rec(24950.0)      # worse for a PE (higher) - must not loosen back up
b.tick_price("NIFTY", 24890.0)
t = b.books["NIFTY"].trade
check("a less favourable (higher) reading does not loosen a PE's stop back up",
      t["index_sl"] == 24895.0, t["index_sl"])

print("7. A MISSING/NaN SUPERTREND READING (INDICATOR STILL WARMING UP) IS SKIPPED, NOT GUESSED")
b = book()
b.books["NIFTY"].trade = mk(use_premium=False, targets=(25100.0, 25140.0, 25200.0), stop=24900.0)
b.books["NIFTY"].last_rec = rec(None)
b.tick_price("NIFTY", 25110.0)                # touches T1 only
t = b.books["NIFTY"].trade
check("T1's own ratchet still fires", t["index_sl"] == 25100.0, t["index_sl"])
check("no crash and no further move with no Supertrend reading at all",
      t["index_sl"] == 25100.0)
b.books["NIFTY"].last_rec = None              # no rec at all yet (fresh book, update() never ran)
b.tick_price("NIFTY", 25110.0)
check("a missing rec entirely is just as safe", b.books["NIFTY"].trade["index_sl"] == 25100.0)

print("8. config.TRAIL_AFTER_T1_SUPERTREND = False DISABLES IT ENTIRELY")
config.TRAIL_AFTER_T1_SUPERTREND = False
b = book()
b.books["NIFTY"].trade = mk(use_premium=False, targets=(25100.0, 25140.0, 25200.0), stop=24900.0)
b.books["NIFTY"].last_rec = rec(25105.0)      # would have trailed past T1 if the flag were on
b.tick_price("NIFTY", 25110.0)
t = b.books["NIFTY"].trade
check("T1's own ratchet still fires (unconditional, unrelated to this flag)", t["index_sl"] == 25100.0)
check("but the flag being off stops the extra trail - stop stays at T1, not 25105",
      t["index_sl"] == 25100.0, t["index_sl"])
config.TRAIL_AFTER_T1_SUPERTREND = was_flag

print()
if fails:
    print(f"SUPERTREND TRAIL LIVE TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("SUPERTREND TRAIL LIVE TEST PASSED")
