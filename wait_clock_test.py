#!/usr/bin/env python3
"""A hold that is a clock says how long is left as a number (25 Sep 2026).

The user: "i dont see that 20 sec holding text or 20 minutes holding text with timers". A ticket is held for a clock in
three places - the direction must hold for SIGNAL_CONFIRM_SECONDS, a second ticket the same way waits
REENTRY_COOLDOWN_MIN minutes after the last, and an optional minimum gap between tickets - and the page said so only as
words ("about 47s to go") that moved when a poll arrived. The state now carries `left_s` (seconds, from the same clock the
gates read) and `base` (the reason without its "about N to go"), so the page can count down every second. Every other
hold has no clock: `left_s` is None. Runs the real TicketBook against a pinned clock, like cooldown_skip_test.py.
"""
import datetime as dt, os, sys, tempfile, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config, tickets, trade_log

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BASE = dt.datetime(2026, 9, 8, 11, 0, 0, tzinfo=IST)
CLOCK = {"s": 0.0}
tickets.now_ist = lambda: BASE + dt.timedelta(seconds=CLOCK["s"])
tickets.time = types.SimpleNamespace(time=lambda: 1_780_000_000 + CLOCK["s"])
tickets.is_market_open = lambda *a, **k: True
tickets._safe_explain = lambda rec: {"verdict": "test"}
trade_log._log_path = lambda: os.path.join(tempfile.mkdtemp(), "trades.csv")

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

NEXT = {"CE": 24500, "PE": 24200}
def rec(side="CE", spot=None):
    ce = side == "CE"
    spot = spot if spot is not None else (24500.0 if ce else 24200.0)
    return {"index": "NIFTY", "bias": "BULLISH" if ce else "BEARISH", "option_type": side,
            "suggested_strike": NEXT[side], "spot": spot,
            "index_targets": [24560., 24610., 24660.] if ce else [24140., 24090., 24040.],
            "index_stop_loss": 24440. if ce else 24260.,
            "premium_targets": [130., 150., 170.], "premium_stop_loss": 80.,
            "premium_source": None, "live_ltp": None, "option_chain": None,
            "risk_points": 60.0, "reach_points": 160.0, "reach_to_risk": 2.67,
            "opening_range": {"ready": True, "high": 24400.0, "low": 24300.0},
            "spread": None, "technical": {"adx": 30}}

book = tickets.TicketBook(owner=None, market="nse_index")
book.auto_rearm = True
def feed(r, seconds=1, n=1):
    opened = []
    for _ in range(n):
        CLOCK["s"] += seconds
        opened += [e for e in book.update("NIFTY", r) if e.get("kind") == "opened"]
    for e in opened:
        NEXT[e["trade"]["option_type"]] += 50
    return opened
W = lambda: book.public("NIFTY")["wait"]

print("1. THE DIRECTION HAS TO HOLD")
need = config.SIGNAL_CONFIRM_SECONDS
feed(rec(), n=31)
w = W()
check("confirming, with the seconds left as a number", w and w["code"] == "confirming" and w["left_s"] is not None, w)
check(f"about {need - 30}s left after 30s of a {need}s window", abs(w["left_s"] - (need - 30)) <= 2, w["left_s"])
check("the base text has no 'about N to go' in it", "about" not in w["base"] and w["base"].endswith("before a ticket is issued."), w["base"])
check("the full text still has it (other readers of it are untouched)", "to go" in w["why"], w["why"])
left1 = w["left_s"]
CLOCK["s"] += 10
check("asked ten seconds later (no new reading) the clock has moved on by ten - it is worked out when asked", abs((left1 - W()["left_s"]) - 10) <= 1, (left1, W()["left_s"]))
check("the window ends and a ticket is issued", len(feed(rec(), n=need)) == 1)

print("2. THE MINUTES A SECOND TICKET WAITS")
feed(rec(spot=24430.0))                                   # through the 24440 stop
check("stopped out", book.books["NIFTY"].trade is None or book.books["NIFTY"].trade["status"] != "OPEN")
feed(rec(), n=need + 5)
w = W()
mins = config.REENTRY_COOLDOWN_MIN
check("the cooldown is a clock in seconds", w and w["code"] == "reentry_cooldown" and w["left_s"] is not None, w)
check(f"it is close to the whole {mins} minutes (minus what has passed since the exit)", 0 < w["left_s"] <= mins * 60 and w["left_s"] > mins * 60 - 400, w["left_s"])
check("its base text has no 'about N to go' either, and the sentence still reads", "about" not in w["base"] and "so a stop-out is not bought straight back" in w["base"] and "waits" in w["base"], w["base"])
CLOCK["s"] += 600
check("ten minutes later, ten minutes less", abs(w["left_s"] - W()["left_s"] - 600) <= 2, (w["left_s"], W()["left_s"]))

print("3. HOLDS THAT ARE NOT A CLOCK SAY NOTHING ABOUT ONE")
CLOCK["s"] += 4000
feed(rec("PE"), n=need + 5)
w = W()
check("whatever holds it now, a hold with no clock has none", w is None or w["code"] in ("confirming", "reentry_cooldown", "ticket_gap") or w["left_s"] is None, w)
book2 = tickets.TicketBook(owner=None, market="nse_index")
check("no index yet: no wait at all", book2.public("NIFTY")["wait"] is None)
b = book.books["NIFTY"]
b.wait_reason = ("position_open", "POSITION OPEN", "A ticket is already running on this index.")
w = W()
check("an open position's hold is only words: no clock", w["left_s"] is None and w["base"] == w["why"], w)
b.wait_reason = ("neutral", "NO SIGNAL", "The indicators do not agree on a direction yet.")
check("no signal: no clock", W()["left_s"] is None)
b.wait_reason = ("confirming", "CONFIRMING", "Waiting for 4 agreeing readings - 1 so far.")
b.confirm_since = None
check("waiting on readings, not seconds: no clock, the words unchanged", W()["left_s"] is None and W()["base"].startswith("Waiting for 4"), W())

print("WAIT CLOCK TEST PASSED" if not fails else f"WAIT CLOCK TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
