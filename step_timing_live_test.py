#!/usr/bin/env python3
"""STEP_STOP_AT_CANDLE_CLOSE in tickets.py's TicketBook._check_price() - a target's step to the stop waits for the
15-minute candle that touched it to close (the user, 13 Oct 2026: "when the stop moves to t1 its instantly exiting at
t1 only"; t1_step_timing_study.py measured it). What a step does is trailing_stop_test.py's; this is WHEN. Paper only."""
import datetime as dt
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
os.environ["ENABLE_CRYPTO"] = "1"             # a crypto-market book has its instruments, as the server runs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import tickets

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_now = {"t": dt.datetime(2026, 10, 14, 10, 7, 0, tzinfo=IST)}
tickets.now_ist = lambda: _now["t"]
def at(hh, mm, ss=0):
    _now["t"] = dt.datetime(2026, 10, 14, hh, mm, ss, tzinfo=IST)

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def book():
    d = tempfile.mkdtemp()
    return tickets.TicketBook(owner=None, market="nse_index", path=os.path.join(d, "t.csv"))


def mk(entry=90.0, targets=(105.0, 114.0, 120.0), stop=70.0, exit_at="T3", option_type="CE"):
    return {"index": "SENSEX", "option_type": option_type, "strike": 72000, "entry_time": "10:00:00",
            "entry_spot": 72000.0, "entry_ts": _now["t"] - dt.timedelta(minutes=7), "entry_ltp": entry,
            "use_premium": True, "lot_size": 20, "lots": 1, "exit_at": exit_at,
            "index_targets": [], "index_sl": None, "premium_targets": list(targets), "premium_sl": stop,
            "hit": {"T1": False, "T2": False, "T3": False}, "hit_time": {"T1": None, "T2": None, "T3": None},
            "sl_hit": False, "sl_hit_time": None, "time_breakeven_done": False,
            "status": "OPEN", "trade_id": "SENSEX-steptest"}


saved = config.STEP_STOP_AT_CANDLE_CLOSE
was_trail = config.TRAIL_AFTER_T1_SUPERTREND
config.STEP_STOP_AT_CANDLE_CLOSE = 15
check("the setting as it ships: the step waits for the 15-minute candle", saved == 15, saved)

print("1. T1 TOUCHED AT 10:07: THE STOP STAYS WHERE IT WAS UNTIL THAT CANDLE CLOSES (10:15)")
at(10, 7)
b = book()
b.books["SENSEX"].trade = mk()
b.tick_price("SENSEX", 106.0)                              # touches T1 (105)
t = b.books["SENSEX"].trade
check("T1 marked as touched", t["hit"]["T1"] is True)
check("...but the stop has NOT moved yet", t["premium_sl"] == 70.0, t["premium_sl"])
check("...the step waits, due at 10:15", [(p["rung"], p["level"], p["at"].strftime("%H:%M")) for p in t["stop_pending"]]
      == [("T1", 105.0, "10:15")], t.get("stop_pending"))
pub = b._ticket_public(b.books["SENSEX"]) if hasattr(b, "_ticket_public") else None
at(10, 7, 20)
b.tick_price("SENSEX", 104.2)                              # the flicker back under T1 - the live complaint
t = b.books["SENSEX"].trade
check("a dip under T1 a few seconds later does NOT close it (the 13 Oct live problem)", t["status"] == "OPEN", t["status"])
at(10, 15, 1)
evs = b.tick_price("SENSEX", 107.0)                        # first tick of the next candle
t = b.books["SENSEX"].trade
check("at 10:15 the step takes effect: the stop is T1", t["premium_sl"] == 105.0 and t["stepped"] == ["T1"]
      and not t["stop_pending"], (t["premium_sl"], t.get("stepped"), t.get("stop_pending")))
at(10, 21)
b.tick_price("SENSEX", 104.0)                              # a real fall back under T1 in a later candle
t = b.books["SENSEX"].trade
check("a fall under T1 in a LATER candle closes it at the T1 stop", "trailed to T1" in t["status"], t["status"])

print("2. THE ORIGINAL STOP STILL GUARDS THE TRADE WHILE THE STEP WAITS")
at(10, 37)
b = book()
b.books["SENSEX"].trade = mk()
b.tick_price("SENSEX", 106.0)                              # T1 touched at 10:37 - step due 10:45
b.tick_price("SENSEX", 69.0)                               # a collapse through the original stop before 10:45
t = b.books["SENSEX"].trade
check("closed by the ORIGINAL stop - and not called trailed (the step never took effect)",
      "stop-loss hit" in t["status"] and "trailed" not in t["status"], t["status"])

print("3. A T3 EXIT: THE T2 STEP WAITS TOO, AND THE TARGET STILL CLOSES AT ONCE")
at(11, 2)
b = book()
b.books["SENSEX"].trade = mk()
b.tick_price("SENSEX", 115.0)                              # T1 and T2 in one tick - both steps due 11:15
t = b.books["SENSEX"].trade
check("both steps wait for 11:15", sorted(p["rung"] for p in t["stop_pending"]) == ["T1", "T2"]
      and t["premium_sl"] == 70.0, t.get("stop_pending"))
at(11, 15, 2)
b.tick_price("SENSEX", 116.0)
t = b.books["SENSEX"].trade
check("at 11:15 the stop is T2, the tighter of the two", t["premium_sl"] == 114.0, t["premium_sl"])
at(11, 20)
b.tick_price("SENSEX", 121.0)
t = b.books["SENSEX"].trade
check("T3 closes the trade the moment it prints - the target never waits", "T3 hit" in t["status"], t["status"])

print("4. THE SUPERTREND TRAIL STARTS WHEN T1'S STEP DOES, NOT ON THE TOUCH")
config.TRAIL_AFTER_T1_SUPERTREND = True
at(12, 3)
b = book()
b.books["SENSEX"].trade = mk()
rec_st = {"supertrend": 72100.0}                           # well above entry in index points: a tight trail level
b._check_price(b.books["SENSEX"], 106.0, rec_st)          # touch T1 at 12:03
t = b.books["SENSEX"].trade
check("no trail before T1's step has taken effect", t["premium_sl"] == 70.0, t["premium_sl"])
at(12, 15, 1)
b._check_price(b.books["SENSEX"], 107.0, rec_st)
t = b.books["SENSEX"].trade
check("from 12:15 the stop is at least T1, and the trail may lift it further", t["premium_sl"] >= 105.0, t["premium_sl"])

print("5. SET TO 0 IT IS EXACTLY THE OLD BEHAVIOUR - ON THE TOUCH")
config.STEP_STOP_AT_CANDLE_CLOSE = 0
at(13, 7)
b = book()
b.books["SENSEX"].trade = mk()
b.tick_price("SENSEX", 106.0)
t = b.books["SENSEX"].trade
check("0: the stop is T1 on the touching tick, nothing waits", t["premium_sl"] == 105.0 and not t.get("stop_pending"),
      (t["premium_sl"], t.get("stop_pending")))

print("5b. ONLY THE INDIAN RULES' TICKETS WAIT - THE AI DESK'S AND EXNESS CFDS STEP ON THE TOUCH, AS MEASURED")
config.STEP_STOP_AT_CANDLE_CLOSE = 15
at(13, 22)
b = book()
b.books["SENSEX"].trade = dict(mk(), ai_reason="the model's own reason")
b.tick_price("SENSEX", 106.0)
t = b.books["SENSEX"].trade
check("an AI-desk ticket: the stop is T1 on the touch", t["premium_sl"] == 105.0 and not t.get("stop_pending"),
      (t["premium_sl"], t.get("stop_pending")))
b = book()
b.books["SENSEX"].trade = dict(mk(), cfd=True)
b.tick_price("SENSEX", 106.0)
t = b.books["SENSEX"].trade
check("a CFD ticket: on the touch", t["premium_sl"] == 105.0 and not t.get("stop_pending"), t["premium_sl"])
d = tempfile.mkdtemp()
cb = tickets.TicketBook(owner=None, market="crypto", path=os.path.join(d, "t.csv"))
name = next(iter(cb.books))
cb.books[name].trade = dict(mk(), index=name)
cb.tick_price(name, 106.0)
t = cb.books[name].trade
check("a crypto-market book: on the touch", t["premium_sl"] == 105.0 and not t.get("stop_pending"), t["premium_sl"])

print("5c. A TREND RIDER TICKET ON THE RULES' EXIT (TREND_RIDER_EXIT, the user's choice 13 Oct 2026) STEPS THE SAME WAY")
config.STEP_STOP_AT_CANDLE_CLOSE = 15
was_exit = config.TREND_RIDER_EXIT
config.TREND_RIDER_EXIT = "rules"
at(9, 52)
b = book()
b.books["SENSEX"].trade = dict(mk(), plain_exit=True, rule_strategy=True, system="trend_rider")
b.tick_price("SENSEX", 106.0)
t = b.books["SENSEX"].trade
check("T1 touched at 09:52: the step waits for 10:00, no near-target ladder", t["premium_sl"] == 70.0
      and [p["rung"] for p in t["stop_pending"]] == ["T1"] and not t.get("near_steps"), (t["premium_sl"], t.get("stop_pending")))
at(9, 53)
b.tick_price("SENSEX", 112.5)                              # past 90% of the way to T2 (111.6) - the ladder's trigger
t = b.books["SENSEX"].trade
check("past the ladder's 90%-to-T2 trigger: on the rules' exit the ladder does NOT fire - the stop waits for the "
      "candle, and the page is shown no ladder", t["premium_sl"] == 70.0 and not t.get("near_steps")
      and tickets.stop_steps(t) == [], (t["premium_sl"], t.get("near_steps"), tickets.stop_steps(t)))
at(10, 0, 1)
b.tick_price("SENSEX", 107.0)
check("at 10:00 its stop is T1", b.books["SENSEX"].trade["premium_sl"] == 105.0, b.books["SENSEX"].trade["premium_sl"])
config.TREND_RIDER_EXIT = "ladder"
b = book()
b.books["SENSEX"].trade = dict(mk(), plain_exit=True, rule_strategy=True, system="trend_rider")
b.tick_price("SENSEX", 106.0)
at(10, 15, 1)
b.tick_price("SENSEX", 107.0)
t = b.books["SENSEX"].trade
check("on the ladder a T1 touch moves nothing (only its near-target steps do)", t["premium_sl"] == 70.0
      and not t.get("stop_pending"), (t["premium_sl"], t.get("stop_pending")))
config.TREND_RIDER_EXIT = was_exit

print("6. THE PAGE IS TOLD WHAT IS WAITING")
config.STEP_STOP_AT_CANDLE_CLOSE = 15
at(14, 1)
b = book()
b.books["SENSEX"].trade = mk()
b.tick_price("SENSEX", 106.0)
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tickets.py")).read()
check("the ticket's public state carries stop_pending (rung, level, HH:MM) and the rungs that stepped",
      '"stop_pending": [{"rung": p["rung"], "level": p["level"], "at": p["at"].strftime("%H:%M")}' in src
      and '"stepped": list(trade.get("stepped") or []),' in src)

config.STEP_STOP_AT_CANDLE_CLOSE = saved
config.TRAIL_AFTER_T1_SUPERTREND = was_trail
print()
print("STEP TIMING LIVE TEST PASSED" if not fails else f"STEP TIMING LIVE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
