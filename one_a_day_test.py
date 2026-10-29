#!/usr/bin/env python3
"""One trade a day per index (config.DAY_TRADES_CHOICES; the user, 29 Oct 2026: "yes add the one trade a day switch"):
off by default; on "one", once a ticket has opened on that index today the next waits for tomorrow - every open path
(it sits in entry_block), whatever the daily-limits switch says, read off the trade log so a restart forgets nothing.
Real TicketBook, pinned clock; no network."""
import datetime as dt
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config
import tickets
import trade_log

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BASE = dt.datetime(2026, 10, 29, 10, 0, 0, tzinfo=IST)
CLOCK = {"s": 0.0}
tickets.now_ist = lambda: BASE + dt.timedelta(seconds=CLOCK["s"])
tickets.time = types.SimpleNamespace(time=lambda: 1_780_000_000 + CLOCK["s"])
tickets.is_market_open = lambda *a, **k: True
tickets._safe_explain = lambda rec: {"verdict": "test"}

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def rec(strike, spot=24560.0):
    return {"index": "NIFTY", "bias": "BULLISH", "option_type": "CE", "suggested_strike": strike, "spot": spot,
            "index_targets": [spot + 60, spot + 120, spot + 180], "index_stop_loss": spot - 60,
            "premium_targets": [150., 170., 190.], "premium_stop_loss": 100., "premium_source": None, "live_ltp": None,
            "option_chain": None, "risk_points": 60.0, "reach_points": 180.0, "reach_to_risk": 3.0,
            "opening_range": {"ready": True, "high": 24580.0, "low": 24500.0}, "spread": None,
            "technical": {"adx": 30}}


def book():
    path = os.path.join(tempfile.mkdtemp(), "trades.csv")
    trade_log._log_path = lambda: path
    return tickets.TicketBook(owner=None, market="nse_index", path=path)


def feed(b, r, n):
    evs = []
    for _ in range(n):
        CLOCK["s"] += 1
        evs += b.update("NIFTY", dict(r))
    return evs


N = config.SIGNAL_CONFIRM_SECONDS + config.SIGNAL_CONFIRM_TICKS + 5

print("1. OFF BY DEFAULT")
check("config: any, by default", config.DAY_TRADES_CHOICES == ("any", "one") and config.DAY_TRADES_DEFAULT == "any")
b = book()
check("every index starts on any", [b.day_trades_for(k) for k in ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")]
      == ["any"] * 4)
check("the page gets it", b.session()["day_trades_by"]["NIFTY"] == "any"
      and b.session()["day_trades_choices"] == ["any", "one"])
b.configure(day_trades="one", day_trades_index="NIFTY")
b.configure(day_trades="three", day_trades_index="SENSEX")
check("set per index, saved; an unknown choice ignored", b.day_trades_for("NIFTY") == "one"
      and tickets.TicketBook(owner=None, market="nse_index", path=b.path).day_trades_for("NIFTY") == "one"
      and b.day_trades_for("SENSEX") == "any")


def day(choice):
    """A ticket, stopped out, then 30 minutes later the same signal on another strike: what happens?"""
    CLOCK["s"] = 0.0
    b = book()
    b.configure(day_trades=choice, day_trades_index="NIFTY")
    first = [e for e in feed(b, rec(24550), N) if e.get("kind") == "opened"]
    b.tick_price("NIFTY", 24490.0)                         # through its stop
    closed = b.books["NIFTY"].trade is None or b.books["NIFTY"].trade.get("status") != "OPEN"
    CLOCK["s"] += 1800
    second = [e for e in feed(b, rec(24600, spot=24610.0), N) if e.get("kind") == "opened"]
    return b, first, closed, second


print("2. ONE A DAY")
b, first, closed, second = day("one")
check("the day's first ticket opens", len(first) == 1)
check("...and is stopped out", closed)
check("the next signal, 30 minutes later on another strike: no ticket", not second, [e["trade"]["strike"] for e in second])
check("...and the reason says so", (b.books["NIFTY"].wait_reason or ("",))[0] == "one_a_day"
      and "next one is tomorrow" in b.books["NIFTY"].wait_reason[2], b.books["NIFTY"].wait_reason)
b.configure(day_trades="one", day_trades_index="SENSEX")
check("another index on one a day is not held by Nifty's ticket", b.entry_block("SENSEX") is None)
check("a fresh book on the same log (a restart) still holds it", (tickets.TicketBook(
      owner=None, market="nse_index", path=b.path).entry_block("NIFTY") or ("",))[0] == "one_a_day")
CLOCK["s"] += 86400
check("tomorrow, it is free again", b.entry_block("NIFTY") is None)

print("3. ANY: THE SAME DAY OPENS THE SECOND TICKET")
b, first, closed, second = day("any")
check("first ticket, stopped, then the second opens", len(first) == 1 and closed and len(second) == 1,
      (len(first), closed, len(second)))

print("4. EVERY OPEN PATH, WHATEVER THE LIMITS SWITCH")
src = open(os.path.join(HERE, "tickets.py")).read()
i = src.index("    def entry_block(self, index=None):")
block = src[i:src.index("\n    def ", i + 10)]
check("it sits in entry_block, ahead of the limits switch",
      block.index('self.day_trades_for(index) == "one"') < block.index("if not self.limits:"))
check("entry_block is asked by the rules, the rule path (the big candle too) and the re-arm",
      src.count("self.entry_block(book.name)") == 3)

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
