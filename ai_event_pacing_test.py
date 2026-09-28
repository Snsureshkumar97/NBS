#!/usr/bin/env python3
"""The AI desk's off-cycle look pacing survives a restart (28 Sep 2026).

The user found duplicate "entry" decisions on the same index seconds apart in the live log - one
answered "Identical snapshot to the wait I logged seconds ago." event_entries (the day's count of
off-cycle looks) and last_event_entry / event_reviews / last_event_review (the clocks that enforce the
5-minute gap between them) lived only in memory: every restart forgot both how recently one had just
been asked and how many the day had already spent, so the first off-cycle look after ANY restart went
out ungated. This pins that they are now saved and reloaded, exactly as last_candle/last_exit already
were, and that a restart can no longer defeat the gap or the daily cap it is meant to enforce.

Fakes only - a scripted model, a fake feed, a clock the test moves, a temp log folder.
"""
import datetime as dt
import json
import os
import sys
import tempfile
import threading

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_desk as ad
import config
import market_bot as mb
import tickets

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
T = {"now": dt.datetime(2026, 9, 28, 10, 0, 50, tzinfo=IST), "clock": 1_790_000_000.0}
tickets.now_ist = lambda: T["now"]


def rec(name="NIFTY"):
    return {"index": name, "spot": 25010.0, "bias": "BULLISH", "option_type": "CE", "confidence": "Medium",
            "suggested_strike": 25000, "option_chain": None, "technical": {"adx": 24.0}}


class Rules:
    lots, capital, risk_pct = 2, None, 1.0


class Streamer:
    def price(self, tok): return None


class FakeFeed:
    def __init__(self, email, market="nse_index"):
        self.market, self.email, self.key = market, email, f"{email}#{market}"
        self.lock = threading.RLock()
        self.tickets = Rules()
        self.streamer = Streamer()
        self.dstream = None
        self.faults = []
        self.live_at = __import__("time").time()
        names = config.instruments_in(market)
        self.state = {"feed": "ok", "indices": {n: {"rec": rec(n)} for n in names}}
    def instruments(self):
        return config.instruments_in(self.market)
    def snapshot(self):
        return {"indices": {}, "tickets": {}, "session": {}}
    def _note_fault(self, where, text):
        self.faults.append((where, text))


ASKED = []
def fake_decide(kind, index, context, desk, tools_ctx=None, client=None):
    ASKED.append((kind, index, T["clock"]))
    return {"action": "wait", "reason": "nothing clear"}, {"input_tokens": 1, "output_tokens": 1, "looked_at": []}
mb.decide = fake_decide
mb.key_present = lambda: True

EMAIL = "restart-pacing@example.invalid"


def new_desk():
    """A fresh AIDesk pointed at the SAME on-disk state as any earlier one for this email - what a
    process restart actually looks like: a new object, the old file."""
    f = FakeFeed(EMAIL)
    return f, ad.AIDesk(f, now=lambda: T["now"], clock=lambda: T["clock"], start=False)


print("1. THE COUNTERS ARE WRITTEN, AND READ BACK BY A FRESH OBJECT AT THE SAME PATH")
f1, d1 = new_desk()
d1.event_entries = {"NIFTY": 3, "BANKNIFTY": 1}
d1.last_event_entry = {"NIFTY": 1_790_000_100.0}
d1.event_reviews = {"NIFTY-20260928-100000": 2}
d1.last_event_review = {"NIFTY": 1_790_000_050.0}
d1._save()
f2, d2 = new_desk()
check("event_entries survives", d2.event_entries == {"NIFTY": 3, "BANKNIFTY": 1}, d2.event_entries)
check("last_event_entry survives", d2.last_event_entry == {"NIFTY": 1_790_000_100.0}, d2.last_event_entry)
check("event_reviews survives (keyed by trade_id)", d2.event_reviews == {"NIFTY-20260928-100000": 2}, d2.event_reviews)
check("last_event_review survives", d2.last_event_review == {"NIFTY": 1_790_000_050.0}, d2.last_event_review)

print("2. AN OLD STATE FILE (SAVED BEFORE 28 Sep 2026) LOADS WITHOUT THESE KEYS - NO CRASH, EMPTY DICTS")
old = json.load(open(d1.state_path))
for k in ("event_entries", "last_event_entry", "event_reviews", "last_event_review"):
    old.pop(k, None)
json.dump(old, open(d1.state_path, "w"))
f3, d3 = new_desk()
check("falls back to empty dicts, not an exception", d3.event_entries == {} and d3.last_event_entry == {}
      and d3.event_reviews == {} and d3.last_event_review == {})

print("3. THE ACTUAL BUG: A RESTART NO LONGER DEFEATS THE 5-MINUTE GAP")
f4, d4 = new_desk()
d4.enabled = {"NIFTY": True}
with d4.lock:
    d4.pending_entry["NIFTY"] = ["ADX crossed above the trend gate"]
d4._event_entries()
check("the first off-cycle look goes out", len(ASKED) == 1 and d4.event_entries.get("NIFTY") == 1, ASKED)
d4._save()

T["clock"] += 5.0          # 5 seconds later - a "restart" happens right here, the way today's did
f5, d5 = new_desk()         # a brand-new object, loading the file d4 just wrote
d5.enabled = {"NIFTY": True}
with d5.lock:
    d5.pending_entry["NIFTY"] = ["ADX crossed above the trend gate"]   # the SAME crossing, still pending
d5._event_entries()
check("...the SAME index, 5 seconds later, through a fresh object at the same path: still gated - no second ask",
      len(ASKED) == 1, ASKED)
check("its own gap check now sees the timestamp loaded from d4's save, not a blank one",
      d5.last_event_entry.get("NIFTY") == 1_790_000_000.0, d5.last_event_entry)

T["clock"] += ad.EVENT_ENTRY_GAP_S   # past the real 5-minute gap this time
f6, d6 = new_desk()
d6.enabled = {"NIFTY": True}
with d6.lock:
    d6.pending_entry["NIFTY"] = ["ADX crossed above the trend gate"]
d6._event_entries()
check("...and once the gap genuinely has passed, a fresh object at the same path asks again, correctly",
      len(ASKED) == 2, ASKED)

print("4. THE DAY'S CAP ALSO SURVIVES A RESTART (not just reset to zero by one)")
f7, d7 = new_desk()
d7.enabled = {"NIFTY": True}
d7.event_entries = {"NIFTY": ad.MAX_EVENT_ENTRIES}       # already at today's cap, loaded from disk
with d7.lock:
    d7.pending_entry["NIFTY"] = ["Price crossed VWAP"]
before = len(ASKED)
d7._event_entries()
check("a fresh object that loads an already-exhausted cap does not ask again today", len(ASKED) == before, ASKED)

print("5. A GENUINE NEW DAY STILL RESETS THE DAY'S COUNT (unaffected by this fix)")
f8, d8 = new_desk()
d8.event_entries = {"NIFTY": 5}
d8.day = "2026-09-27"
T["now"] = dt.datetime(2026, 9, 28, 9, 0, 0, tzinfo=IST)
d8._roll_day()
check("event_entries is zeroed on a new day, same as before", d8.event_entries == {}, d8.event_entries)

print("AI EVENT PACING TEST PASSED" if not fails else f"AI EVENT PACING TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
