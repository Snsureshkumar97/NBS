#!/usr/bin/env python3
"""What the ticket says when the market is shut - and that it says the right thing.

17 Sep 2026: at 08:52 on a trading morning the hold read "The session is over ...
nothing is issued outside 09:15-15:40", and was taken for the opening-range wait
switched off the day before. Before the open it must say so; after the close, on
a weekend or a holiday it is "market closed"; crypto is never held by the clock.
"""
import datetime as dt
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()      # nothing touches the real logs
os.environ["ENABLE_CRYPTO"] = "1"      # so a crypto book really holds crypto, as on the server
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main
import tickets

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def hold_at(when, market=None):
    tickets.now_ist = lambda: when
    book = tickets.TicketBook(market=market) if market else tickets.TicketBook()
    return book.entry_block()


tue_morning = dt.datetime(2026, 9, 8, 8, 52, tzinfo=IST)       # a Tuesday, not a holiday
check("8 Sep 2026 is a trading day (the fixture's own premise)",
      tue_morning.weekday() == 1 and not main.is_nse_holiday(tue_morning.date()))

code, badge, why = hold_at(tue_morning)
check("before the open it says BEFORE THE OPEN", (code, badge) == ("closed", "BEFORE THE OPEN"), badge)
check("...and when it opens", "opens at 09:15" in why, why)
check("...and never 'the session is over'", "session is over" not in why)
check("nothing reads like a 09:15-09:45 wait", "09:45" not in why and "opening range" not in why.lower())

code, badge, why = hold_at(dt.datetime(2026, 9, 8, 16, 5, tzinfo=IST))
check("after the close it is MARKET CLOSED", badge == "MARKET CLOSED", badge)
check("...with the hours written out, not a hyphenated pair", "from 09:15 to 15:40" in why
      and "09:15-15:40" not in why, why)

code, badge, why = hold_at(dt.datetime(2026, 9, 12, 8, 52, tzinfo=IST))      # a Saturday
check("a weekend morning is MARKET CLOSED, not before the open", badge == "MARKET CLOSED", badge)

check("during the session the clock holds nothing",
      (hold_at(dt.datetime(2026, 9, 8, 10, 30, tzinfo=IST)) or ("", "", ""))[0] != "closed")

try:
    crypto = hold_at(dt.datetime(2026, 9, 12, 3, 0, tzinfo=IST), market="crypto")
    check("crypto is never held by the clock, even at 3am on a Saturday", (crypto or ("", "", ""))[0] != "closed", crypto)
except TypeError:
    check("crypto check could not build a crypto book with this TicketBook signature", False)

print("MARKET HOURS WORDING TEST PASSED" if not fails else f"MARKET HOURS WORDING TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
