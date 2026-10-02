#!/usr/bin/env python3
"""What the ticket says when the market is shut - and that it says the right thing.

17 Sep 2026: at 08:52 on a trading morning the hold read "The session is over ...
nothing is issued outside 09:15-15:40", and was taken for the opening-range wait
switched off the day before. Before the open it must say so; after the close, on
a weekend or a holiday it is "market closed"; crypto is never held by the clock.

2 Oct 2026: the user, on an actual NSE holiday that day - "the tool should give
a message... why the market closed today." A holiday and a weekend used to read
as the exact same generic "the session is over", with no way to tell one from
the other. A holiday now names itself (main.nse_holiday_name()).
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
check("a plain weekend names no holiday - it needs no excuse", "holiday" not in why.lower(), why)

# The user, 2 Oct 2026, on an actual holiday that day: "the tool should give a
# message... why the market closed today." 2 Oct 2026 is Gandhi Jayanti AND a
# Friday - an otherwise ordinary trading day - so this is the one case the
# weekend/after-close messages above could never have caught.
code, badge, why = hold_at(dt.datetime(2026, 10, 2, 11, 0, tzinfo=IST))
check("a holiday on a weekday reads MARKET CLOSED, same badge as any other closed session",
      badge == "MARKET CLOSED", badge)
check("...but THIS time it actually names the reason - NSE's own name for the day, not a shrug",
      "Gandhi Jayanti" in why and "NSE trading holiday" in why, why)

code, badge, why = hold_at(dt.datetime(2026, 10, 2, 8, 0, tzinfo=IST))      # before the normal 09:15 open
check("even before the normal opening time, a holiday morning is MARKET CLOSED, not BEFORE THE OPEN - "
      "there is no open to wait for today",
      badge == "MARKET CLOSED" and "Gandhi Jayanti" in why, (badge, why))

check("during the session the clock holds nothing",
      (hold_at(dt.datetime(2026, 9, 8, 10, 30, tzinfo=IST)) or ("", "", ""))[0] != "closed")

try:
    crypto = hold_at(dt.datetime(2026, 9, 12, 3, 0, tzinfo=IST), market="crypto")
    check("crypto is never held by the clock, even at 3am on a Saturday", (crypto or ("", "", ""))[0] != "closed", crypto)
except TypeError:
    check("crypto check could not build a crypto book with this TicketBook signature", False)

print("_consider(): THE MARKET BEING CLOSED IS CHECKED BEFORE 'THE INDICATORS DO NOT AGREE' - NOT MASKED BY IT")
# entry_block() being right is not enough on its own - _consider() has to actually
# reach it. It used to ask "does the signal have no direction?" FIRST, so whenever
# the last reading before a closed session happened to be NEUTRAL (exactly the state
# a quiet, closed feed sits in), the holiday/closed reason was never shown at all -
# masked by "the indicators do not agree on a direction yet", purely by chance.
tb = tickets.TicketBook()
ib = tickets.IndexBook("NIFTY")
check("the fixture's own premise: a fresh IndexBook has no direction yet", ib.confirm_dir is None)
_real_now_ist = tickets.now_ist
try:
    tickets.now_ist = lambda: dt.datetime(2026, 10, 2, 11, 0, tzinfo=IST)     # Gandhi Jayanti, a Friday
    tb._consider(ib, {})
    check("the holiday reason wins even though direction is None - no longer masked",
          ib.wait_reason[1] == "MARKET CLOSED" and "Gandhi Jayanti" in ib.wait_reason[2], ib.wait_reason)

    tickets.now_ist = lambda: dt.datetime(2026, 9, 8, 10, 30, tzinfo=IST)    # an ordinary Tuesday, mid-session
    tb._consider(ib, {})
    check("during an ORDINARY open session, direction still None still reads NO SIGNAL exactly as before - "
          "this only changes what happens when the market is ALSO closed",
          ib.wait_reason[1] == "NO SIGNAL", ib.wait_reason)
finally:
    tickets.now_ist = _real_now_ist

print("main.nse_holiday_name() DIRECTLY")
check("a known holiday returns NSE's own published name", main.nse_holiday_name(dt.date(2026, 10, 2)) == "Gandhi Jayanti",
      main.nse_holiday_name(dt.date(2026, 10, 2)))
check("an ordinary trading day returns None, not an empty string or a guess",
      main.nse_holiday_name(dt.date(2026, 10, 1)) is None)
check("a weekend that happens not to be a listed holiday also returns None - weekends are not this function's job",
      main.nse_holiday_name(dt.date(2026, 9, 12)) is None)
check("a year the calendar does not cover at all degrades to None, the same 'never guess' rule is_nse_holiday() follows",
      main.nse_holiday_name(dt.date(2031, 10, 2)) is None)
check("is_nse_holiday() and nse_holiday_name() agree with each other for every one of this year's listed dates",
      all(main.is_nse_holiday(d) and main.nse_holiday_name(d) for d in main.NSE_HOLIDAYS_BY_YEAR[2026]))

print("MARKET HOURS WORDING TEST PASSED" if not fails else f"MARKET HOURS WORDING TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
