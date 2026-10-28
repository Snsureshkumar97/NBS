#!/usr/bin/env python3
"""config.RULES_EXIT "fixed" in tickets.py (the user, 28 Oct 2026: "change rules exit"; honest_retest.py measured it):
a rules ticket on an Indian index keeps ONE stop - no step to T1 / T2, no 2-hour breakeven, no Supertrend trail - and
is out at its exit target. Frozen on the ticket; the TradingView rating, the Trend Rider and the AI desk keep their
own exit. Also: the TradingView rating offered on all four indices. Paper only."""
import datetime as dt
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config
import tickets

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_now = {"t": dt.datetime(2026, 10, 29, 10, 7, 0, tzinfo=IST)}
tickets.now_ist = lambda: _now["t"]
def at(hh, mm, ss=0):
    _now["t"] = dt.datetime(2026, 10, 29, hh, mm, ss, tzinfo=IST)

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def book():
    return tickets.TicketBook(owner=None, market="nse_index", path=os.path.join(tempfile.mkdtemp(), "t.csv"))


def mk(plan="fixed", **kw):
    t = {"index": "SENSEX", "option_type": "CE", "strike": 72000, "entry_time": "10:00:00", "entry_spot": 72000.0,
         "entry_ts": _now["t"] - dt.timedelta(minutes=7), "entry_ltp": 90.0, "use_premium": True, "lot_size": 20,
         "lots": 1, "exit_at": "T3", "index_targets": [], "index_sl": None, "premium_targets": [105.0, 114.0, 120.0],
         "premium_sl": 70.0, "hit": {"T1": False, "T2": False, "T3": False},
         "hit_time": {"T1": None, "T2": None, "T3": None}, "sl_hit": False, "sl_hit_time": None,
         "time_breakeven_done": False, "status": "OPEN", "trade_id": "SENSEX-fixedtest", "system": "rules"}
    if plan is not None:
        t["rules_exit"] = plan
    t.update(kw)
    return t


print("1. AS IT SHIPS")
check("config.RULES_EXIT is 'steps' (reverted 28 Oct 2026 - realistic option prices); 'fixed' stays available",
      config.RULES_EXIT == "steps", config.RULES_EXIT)
src = open(os.path.join(HERE, "tickets.py")).read()
check("a new ticket freezes the setting (Indian market only)",
      '"rules_exit": _cfg("RULES_EXIT", "steps") if self.market == "nse_index" else "steps"' in src)
check("the Supertrend trail is gated by rules_exit() too",
      'and not trade["sl_hit"] and st is not None and rules_exit(trade)' in src)

print("2. A FIXED RULES TICKET: T1 TOUCHED, THE STOP STAYS")
at(10, 7)
b = book()
b.books["SENSEX"].trade = mk()
b.tick_price("SENSEX", 106.0)                              # touches T1
t = b.books["SENSEX"].trade
check("T1 is marked touched", t["hit"]["T1"] is True)
check("no step waits for the candle close", not t.get("stop_pending"), t.get("stop_pending"))
at(10, 15, 1)
b.tick_price("SENSEX", 107.0)
check("after the candle closes the stop is still the original 70", b.books["SENSEX"].trade["premium_sl"] == 70.0,
      b.books["SENSEX"].trade["premium_sl"])
at(10, 22)
b.tick_price("SENSEX", 100.0)                              # back under T1: a stepped ticket would be out here
check("a fall back under T1 does not close it", b.books["SENSEX"].trade["status"] == "OPEN",
      b.books["SENSEX"].trade["status"])
check("the page says the stop is fixed", b._public_trade(b.books["SENSEX"].trade).get("fixed_stop") is True)
at(10, 40)
b.tick_price("SENSEX", 121.0)                              # T3
check("out at T3", b.books["SENSEX"].trade is None or "T3" in str(b.books["SENSEX"].trade.get("status")),
      (b.books["SENSEX"].trade or {}).get("status"))

print("3. NO 2-HOUR BREAKEVEN")
at(10, 7)
b = book()
b.books["SENSEX"].trade = mk()
at(12, 30)                                                 # 2 h 30 in, T1 never touched, in profit
b.tick_price("SENSEX", 95.0)
check("the stop stays at 70 past two hours", b.books["SENSEX"].trade["premium_sl"] == 70.0,
      b.books["SENSEX"].trade["premium_sl"])

print("4. A T1 TOUCH THAT ENDS AT THE ORIGINAL STOP IS NOT A 'T1 EXIT' (no re-entry waiver)")
at(10, 7)
b = book()
b.books["SENSEX"].trade = mk()
b.tick_price("SENSEX", 106.0)
at(10, 30)
b.tick_price("SENSEX", 69.0)
ib = b.books["SENSEX"]
check("stopped at the original stop", ib.trade is None or ib.trade.get("sl_hit"), (ib.trade or {}).get("status"))
check("last_close_t1 is False", ib.last_close_t1 is False, ib.last_close_t1)

print("5. EVERY OTHER TICKET KEEPS ITS STEPS")
at(10, 7)
for name, kw in (("opened before the switch (no field)", {"plan": None}),
                 ("the setting 'steps'", {"plan": "steps"}),
                 ("an AI desk ticket", {"ai_reason": "model"}),
                 ("a TradingView-rating ticket", {"system": "tv_rating", "plain_exit": True}),
                 ("a Trend Rider ticket (TREND_RIDER_EXIT rules)", {"system": "trend_rider", "plain_exit": True})):
    plan = kw.pop("plan", "fixed")
    tr = mk(plan, **kw)
    check(f"{name}: rules_exit() is True", tickets.rules_exit(tr) is True)
b = book()
b.books["SENSEX"].trade = mk(None)
b.tick_price("SENSEX", 106.0)
check("...and on the live path an old ticket's T1 step waits for the candle as before",
      [p["rung"] for p in (b.books["SENSEX"].trade.get("stop_pending") or [])] == ["T1"])
check("a fixed rules ticket: rules_exit() is False", tickets.rules_exit(mk()) is False)

print("6. THE TRADINGVIEW RATING ON ALL FOUR")
check("offered on Nifty, Bank Nifty, Sensex and Midcap",
      all(tickets.system_allowed("tv_rating", k) for k in ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")))
check("Camarilla still Sensex only", tickets.system_allowed("camarilla", "SENSEX")
      and not tickets.system_allowed("camarilla", "MIDCPNIFTY"))
b = book()
check("each index's choices on the page include it", all("tv_rating" in b.system_choices_for(k)
                                                          for k in ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")))

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
