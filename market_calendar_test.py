#!/usr/bin/env python3
"""The tool shows what the exchange shows: NSE's own holiday list and live status (market_calendar.py).

The user, 20 Oct 2026, on Diwali's Muhurat session (Sunday 8 Nov): "not only for one anything in future it happens it
have show whatever zerodha shows". NSE's list wins for every year it has (a holiday added mid-year, next year's list),
main's own list stands where NSE's has not loaded; a day NSE marks "*" is a SPECIAL SESSION the tool names and sits
out; the closed messages carry NSE's live words. The replies below are NSE's real shapes (read from the server on 20
Oct 2026). No network: nothing here starts the background refresh.
"""
import datetime as dt
import os
import sys
import tempfile
import time

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main
import market_calendar as mc
import tickets

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
D = dt.date
FO_2026 = [("15-Jan-2026", "Municipal Corporation Election - Maharashtra"), ("26-Jan-2026", "Republic Day"),
           ("15-Feb-2026", "Mahashivratri"), ("03-Mar-2026", "Holi"), ("21-Mar-2026", "Id-Ul-Fitr (Ramadan Eid)"),
           ("26-Mar-2026", "Shri Ram Navami"), ("31-Mar-2026", "Shri Mahavir Jayanti"), ("03-Apr-2026", "Good Friday"),
           ("14-Apr-2026", "Dr. Baba Saheb Ambedkar Jayanti"), ("01-May-2026", "Maharashtra Day"),
           ("28-May-2026", "Bakri Id"), ("26-Jun-2026", "Muharram"), ("15-Aug-2026", "Independence Day"),
           ("14-Sep-2026", "Ganesh Chaturthi"), ("02-Oct-2026", "Mahatma Gandhi Jayanti"), ("20-Oct-2026", "Dussehra"),
           ("08-Nov-2026", "Diwali Laxmi Pujan*"), ("10-Nov-2026", "Diwali-Balipratipada"),
           ("24-Nov-2026", "Prakash Gurpurb Sri Guru Nanak Dev"), ("25-Dec-2026", "Christmas")]
HOLIDAYS = {"CM": [], "FO": [{"tradingDate": d, "weekDay": "", "description": n, "morning_session": None,
                              "evening_session": None, "Sr_no": i + 1} for i, (d, n) in enumerate(FO_2026)]}


def status_reply(open_, trade_date):
    return {"marketState": [{"market": "Capital Market", "marketStatus": "Open" if open_ else "Close",
                             "tradeDate": trade_date, "index": "NIFTY 50",
                             "marketStatusMessage": "Market is Open" if open_ else "Market is Closed"},
                            {"market": "Currency", "marketStatus": "Close", "tradeDate": trade_date,
                             "marketStatusMessage": "Market is Closed"}]}


def hold_at(when):
    tickets.now_ist = lambda: when
    return tickets.TicketBook().entry_block()


print("1. BEFORE NSE'S LIST IS READ: main's own list, as before")
check("nothing loaded: no live years", mc.years() == set())
check("Dussehra 20 Oct 2026 is a holiday from main's own list", main.is_nse_holiday(D(2026, 10, 20))
      and main.nse_holiday_name(D(2026, 10, 20)) == "Dussehra")
check("a year neither list has is not guessed at", not main.is_nse_holiday(D(2027, 1, 26)))

print("2. NSE'S LIST READ")
n = mc.ingest_holidays(HOLIDAYS)
check("all 20 of NSE's F&O holidays taken in", n == 20 and mc.years() == {2026}, n)
check("NSE's own names now: 2 Oct is 'Mahatma Gandhi Jayanti'",
      main.nse_holiday_name(D(2026, 10, 2)) == "Mahatma Gandhi Jayanti", main.nse_holiday_name(D(2026, 10, 2)))
check("Muhurat day: named without the '*', and a special session",
      main.nse_holiday_name(D(2026, 11, 8)) == "Diwali Laxmi Pujan"
      and mc.special_session(D(2026, 11, 8)) == "Diwali Laxmi Pujan")
check("an ordinary holiday is not a special session", mc.special_session(D(2026, 10, 20)) is None)
check("a trading day is still a trading day", not main.is_nse_holiday(D(2026, 10, 21)))
check("next open after Dussehra eve is Wednesday 21 Oct", "2026-10-21" in main.next_market_open_str(
      dt.datetime(2026, 10, 19, 16, 0, tzinfo=IST)), main.next_market_open_str(dt.datetime(2026, 10, 19, 16, 0, tzinfo=IST)))

print("3. A BAD OR SHORT REPLY CHANGES NOTHING")
check("garbled reply: nothing taken", mc.ingest_holidays({"oops": 1}) == 0 and mc.ingest_holidays(None) == 0)
check("a year with only 3 entries is left to main's list",
      mc.ingest_holidays({"FO": [{"tradingDate": "26-Jan-2027", "description": "Republic Day"},
                                 {"tradingDate": "03-Mar-2027", "description": "Holi"},
                                 {"tradingDate": "15-Aug-2027", "description": "Independence Day"}]}) == 0
      and mc.years() == {2026} and not main.is_nse_holiday(D(2027, 1, 26)))
check("2026 kept through both", main.nse_holiday_name(D(2026, 10, 20)) == "Dussehra")
check("a holiday NSE adds mid-year is picked up on the next read",
      mc.ingest_holidays({"FO": HOLIDAYS["FO"] + [{"tradingDate": "05-Nov-2026", "description": "Election"}]}) == 21
      and main.is_nse_holiday(D(2026, 11, 5)))
mc.ingest_holidays(HOLIDAYS)
check("...and dropped if NSE drops it", not main.is_nse_holiday(D(2026, 11, 5)))

print("4. NSE'S LIVE STATUS")
when = dt.datetime(2026, 10, 20, 11, 0, tzinfo=IST)
check("the Capital Market row is read", mc.ingest_status(status_reply(False, "19-Oct-2026 15:30"), at=when.timestamp()))
st = mc.status(when)
check("closed, in NSE's own words", st and st["open"] is False and st["text"] == "Market is Closed", st)
check("an old status is not shown (over 10 minutes)", mc.status(when + dt.timedelta(minutes=11)) is None)

print("5. WHAT THE SIGNAL PAGE SAYS")
code, badge, why = hold_at(when)
check("Dussehra: MARKET CLOSED, NSE's name, and NSE's own words",
      badge == "MARKET CLOSED" and "Today is Dussehra" in why and "NSE right now: Market is Closed." in why, why)
muhurat = dt.datetime(2026, 11, 8, 14, 30, tzinfo=IST)
mc.ingest_status(status_reply(True, "08-Nov-2026 14:30"), at=muhurat.timestamp())
code, badge, why = hold_at(muhurat)
check("Muhurat, NSE open: SPECIAL SESSION, named, NSE's words, and that the tool sits it out",
      (code, badge) == ("closed", "SPECIAL SESSION") and "Diwali Laxmi Pujan" in why
      and "NSE right now: Market is Open." in why and "sits special sessions out" in why, why)
code, badge, why = hold_at(dt.datetime(2026, 11, 8, 9, 30, tzinfo=IST))
check("Muhurat day before the session (no fresh status): still SPECIAL SESSION, no stale words",
      badge == "SPECIAL SESSION" and "NSE right now" not in why, why)
sat = dt.datetime(2026, 11, 14, 11, 0, tzinfo=IST)                     # a Saturday NSE opens (a budget day)
mc.ingest_status(status_reply(True, "14-Nov-2026 11:00"), at=sat.timestamp())
code, badge, why = hold_at(sat)
check("a Saturday the exchange says is open: SPECIAL SESSION, not a plain weekend", badge == "SPECIAL SESSION"
      and why.startswith("NSE is holding a special trading session today") and mc.special_session(sat.date(), sat) == "", why)
mc.ingest_status(status_reply(True, "21-Oct-2026 11:00"), at=time.time())
check("an ordinary trading day's 'open' is no special session", mc.special_session(D(2026, 10, 21)) is None)

print()
print("MARKET CALENDAR TEST PASSED" if not fails else f"MARKET CALENDAR TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
