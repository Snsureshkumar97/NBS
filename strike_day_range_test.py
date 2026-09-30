#!/usr/bin/env python3
"""The Signal card's live "Today's range" cell: a strike's own day high/low so
far today. The user, 29 Sep 2026: "the tool does not show me the given strike
high and low ltp of the day when the signal fires." journal.strike_day_range()
only has this after today's close (see its own docstring) - this is the live
equivalent, _strike_day_range_live(), reusing the exact candle fetch
_option_candles() already makes, and _attach_strike_day_range(), which wires
it onto every open ticket's own dict for _api_state() to serve.

Fakes only - no Zerodha/Delta call and no socket.
"""
import os
import sys
import tempfile
import time

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()      # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import config
import feeds
import web_server

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


# One bar from yesterday mixed in first, to prove the today-filter actually
# filters rather than just taking the whole frame.
YEST = pd.date_range("2026-09-28 15:00", periods=1, freq="1min", tz="Asia/Kolkata")
TODAY = pd.date_range("2026-09-29 09:15", periods=4, freq="1min", tz="Asia/Kolkata")
BARS = pd.DataFrame({
    "ts":     list(YEST) + list(TODAY),
    "open":   [999.0, 100.0, 108.0, 95.0,  110.0],
    "high":   [999.0, 105.0, 112.0, 99.0,  115.0],   # today's max high = 115.0
    "low":    [999.0, 98.0,  104.0, 90.0,  108.0],    # today's min low  = 90.0
    "close":  [999.0, 103.0, 109.0, 96.0,  112.0],
    "volume": [1,     10,    20,    5,     30],
})


class FakeProvider:
    def __init__(self, token="TOK-1", rows=BARS, boom_token=None, boom_candles=None):
        self.token, self.rows = token, rows
        self.boom_token, self.boom_candles = boom_token, boom_candles
        self.asked, self.fetched = [], []

    def option_token(self, index_key, strike, option_type, expiry=None):
        self.asked.append((index_key, strike, option_type, expiry))
        if self.boom_token:
            raise RuntimeError(self.boom_token)
        return self.token

    def candles_for_token(self, token, interval="day", days=90):
        self.fetched.append((token, interval, days))
        if self.boom_candles:
            raise RuntimeError(self.boom_candles)
        return self.rows


class ProviderNoCandles:
    """Connected, but too old a build to have candles_for_token at all -
    _option_candles() itself treats this the same as no provider."""
    def option_token(self, *a, **k):
        return "TOK"


class FakeFeed:
    def __init__(self, provider):
        self.prov = provider
    def _provider(self):
        return (self.prov, "ok", "")


def handler():
    h = object.__new__(web_server.Handler)
    return h


NOW = pd.Timestamp("2026-09-29 12:00", tz="Asia/Kolkata")
_real_now_ist = web_server.now_ist
web_server.now_ist = lambda: NOW.to_pydatetime()


def fresh(h):
    """A handler with its own, empty strike-live cache - the cache is a class
    attribute, shared unless each test clears it."""
    h._STRIKE_LIVE_CACHE = {}
    return h


print("1. THE LIVE RANGE ITSELF")
h = fresh(handler())
prov = FakeProvider()
feeds.for_user = lambda email, mkt=None, start=True: FakeFeed(prov)
hi, lo = h._strike_day_range_live("me@example.com", "nse_index", "NIFTY", 23400, "CE", "2026-09-24")
check("today's high and low only - yesterday's bar is excluded",
      (hi, lo) == (115.0, 90.0), (hi, lo))
check("the right contract was asked for", prov.asked == [("NIFTY", 23400, "CE", "2026-09-24")], prov.asked)
check("minute candles, one day, are asked for - the cheapest frame that still covers today",
      prov.fetched == [("TOK-1", "minute", 1)], prov.fetched)

print("2. IT NEVER RAISES")
h = fresh(handler())
feeds.for_user = lambda email, mkt=None, start=True: FakeFeed(FakeProvider(boom_token="no token endpoint"))
check("the option lookup itself failing: (None, None), not an exception",
      h._strike_day_range_live("me@example.com", "nse_index", "NIFTY", 23400, "CE", None) == (None, None))
h = fresh(handler())
feeds.for_user = lambda email, mkt=None, start=True: FakeFeed(FakeProvider(boom_candles="Kite is rate-limited"))
check("the candle fetch itself failing: (None, None)",
      h._strike_day_range_live("me@example.com", "nse_index", "NIFTY", 23400, "CE", None) == (None, None))
h = fresh(handler())
feeds.for_user = lambda email, mkt=None, start=True: (_ for _ in ()).throw(RuntimeError("feed is down"))
check("feeds.for_user itself raising: still (None, None)",
      h._strike_day_range_live("me@example.com", "nse_index", "NIFTY", 23400, "CE", None) == (None, None))

print("3. THE QUIET PATHS THAT GIVE NOTHING RATHER THAN A GUESS")
h = fresh(handler())
feeds.for_user = lambda email, mkt=None, start=True: FakeFeed(FakeProvider())
check("a market that is neither kite nor delta: (None, None), no lookup attempted",
      h._strike_day_range_live("me@example.com", "not-a-real-market", "NIFTY", 23400, "CE", None) == (None, None))
h = fresh(handler())
feeds.for_user = lambda email, mkt=None, start=True: FakeFeed(ProviderNoCandles())
check("a provider with no candles_for_token at all: (None, None)",
      h._strike_day_range_live("me@example.com", "nse_index", "NIFTY", 23400, "CE", None) == (None, None))
h = fresh(handler())
no_token = FakeProvider(token=None)
feeds.for_user = lambda email, mkt=None, start=True: FakeFeed(no_token)
check("no contract found for that strike/expiry: (None, None), and no candle fetch attempted",
      h._strike_day_range_live("me@example.com", "nse_index", "NIFTY", 23400, "CE", None) == (None, None)
      and no_token.fetched == [])
h = fresh(handler())
empty = FakeProvider(rows=BARS.iloc[0:0])
feeds.for_user = lambda email, mkt=None, start=True: FakeFeed(empty)
check("candles come back empty: (None, None)",
      h._strike_day_range_live("me@example.com", "nse_index", "NIFTY", 23400, "CE", None) == (None, None))
h = fresh(handler())
only_yest = FakeProvider(rows=BARS.iloc[[0]])
feeds.for_user = lambda email, mkt=None, start=True: FakeFeed(only_yest)
check("candles come back but none of them are today's: (None, None), not yesterday's range",
      h._strike_day_range_live("me@example.com", "nse_index", "NIFTY", 23400, "CE", None) == (None, None))

print("4. THE CACHE - THE WHOLE REASON THIS IS SAFE TO POLL")
h = fresh(handler())
prov = FakeProvider()
feeds.for_user = lambda email, mkt=None, start=True: FakeFeed(prov)
r1 = h._strike_day_range_live("me@example.com", "nse_index", "NIFTY", 23400, "CE", "2026-09-24")
r2 = h._strike_day_range_live("me@example.com", "nse_index", "NIFTY", 23400, "CE", "2026-09-24")
check("a second call inside the cache window reuses the answer - the provider is asked only once",
      r1 == r2 and len(prov.fetched) == 1, prov.fetched)
check("a DIFFERENT contract is its own cache entry, asked for separately",
      h._strike_day_range_live("me@example.com", "nse_index", "NIFTY", 23500, "CE", "2026-09-24") is not None
      and len(prov.fetched) == 2, prov.fetched)
key = ("NIFTY", 23400, "CE", "2026-09-24")
stamp, val = h._STRIKE_LIVE_CACHE[key]
h._STRIKE_LIVE_CACHE[key] = (stamp - (web_server.Handler._STRIKE_LIVE_CACHE_S + 1), val)
h._strike_day_range_live("me@example.com", "nse_index", "NIFTY", 23400, "CE", "2026-09-24")
check("once the cache entry is older than its own TTL, it is asked again",
      len(prov.fetched) == 3, prov.fetched)

print("5. THE CACHE DOES NOT GROW WITHOUT BOUND")
h = fresh(handler())
prov = FakeProvider()
feeds.for_user = lambda email, mkt=None, start=True: FakeFeed(prov)
for i in range(70):
    h._strike_day_range_live("me@example.com", "nse_index", "NIFTY", 20000 + i, "CE", "2026-09-24")
check("it prunes back down rather than growing forever",
      len(h._STRIKE_LIVE_CACHE) <= 64, len(h._STRIKE_LIVE_CACHE))

print("6. WIRING IT ONTO A TICKET (_attach_strike_day_range)")
def mk_ticket(open_=True, tracked_on="premium", strike=23400, side="CE"):
    return {"open": open_, "tracked_on": tracked_on, "strike": strike, "option_type": side, "expiry": "2026-09-24"}

class Recording:
    """Stands in for _strike_day_range_live so this section tests the LOOP
    and the MUTATION, not the lookup itself (that is sections 1-5)."""
    def __init__(self, value=(115.0, 90.0)):
        self.calls, self.value = [], value
    def __call__(self, user, market, key, strike, side, expiry):
        self.calls.append((user, market, key, strike, side, expiry))
        return self.value

h = handler()
rec = Recording()
h._strike_day_range_live = rec
t_open = mk_ticket()
tickets = {"NIFTY": {"ticket": t_open}}
h._attach_strike_day_range("me@example.com", "nse_index", tickets)
check("an open, premium-tracked ticket is asked for and gets the range attached",
      rec.calls == [("me@example.com", "nse_index", "NIFTY", 23400, "CE", "2026-09-24")]
      and t_open["strike_day_high"] == 115.0 and t_open["strike_day_low"] == 90.0, t_open)

h = handler()
rec = Recording()
h._strike_day_range_live = rec
t_closed = mk_ticket(open_=False)
h._attach_strike_day_range("me@example.com", "nse_index", {"NIFTY": {"ticket": t_closed}})
check("a closed ticket is left alone - no lookup, no fields added",
      rec.calls == [] and "strike_day_high" not in t_closed, t_closed)

h = handler()
rec = Recording()
h._strike_day_range_live = rec
t_index = mk_ticket(tracked_on="index")
h._attach_strike_day_range("me@example.com", "nse_index", {"NIFTY": {"ticket": t_index}})
check("an index-tracked ticket is left alone - it has no premium to range",
      rec.calls == [] and "strike_day_high" not in t_index, t_index)

h = handler()
rec = Recording()
h._strike_day_range_live = rec
t_none = {"open": True, "tracked_on": "premium"}    # no strike/side at all
h._attach_strike_day_range("me@example.com", "nse_index", {"NIFTY": {"ticket": t_none}})
check("a ticket with no strike or side is skipped rather than asked with junk",
      rec.calls == [], rec.calls)

h = handler()
rec = Recording()
h._strike_day_range_live = rec
h._attach_strike_day_range("me@example.com", "nse_index", {"NIFTY": None, "BANKNIFTY": {"ticket": None}, "SENSEX": {}})
check("nothing open anywhere at all: no lookups, no crash",
      rec.calls == [], rec.calls)

h = handler()
rec = Recording(value=(None, None))
h._strike_day_range_live = rec
t_unknown = mk_ticket()
h._attach_strike_day_range("me@example.com", "nse_index", {"NIFTY": {"ticket": t_unknown}})
check("the lookup itself returning (None, None) - not a fetch failure, just nothing recorded yet - is passed through as None, not dropped or defaulted",
      t_unknown["strike_day_high"] is None and t_unknown["strike_day_low"] is None, t_unknown)

h = handler()
rec = Recording()
h._strike_day_range_live = rec
two = {"NIFTY": {"ticket": mk_ticket(strike=23400)},
       "BANKNIFTY": {"ticket": mk_ticket(strike=50000)}}
h._attach_strike_day_range("me@example.com", "nse_index", two)
check("more than one open ticket: each is asked for, by its own index name",
      sorted((c[2], c[3]) for c in rec.calls) == [("BANKNIFTY", 50000), ("NIFTY", 23400)], rec.calls)

web_server.now_ist = _real_now_ist
print("STRIKE DAY RANGE TEST PASSED" if not fails else f"STRIKE DAY RANGE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
