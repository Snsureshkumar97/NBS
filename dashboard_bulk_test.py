#!/usr/bin/env python3
"""The Dashboard's two bulk buttons, 29 Sep 2026: "add two button where i can turn on live
trades for all the market with one button and other with clear all positions that exits all
the trades in all the markets" - then, on being asked: "indian and crytp is separate", meaning
per-market, not spanning nse_index and crypto in one click.

Three things, tested at the level each actually lives at:
  1. TicketBook.clear_all() (tickets.py) - the bulk form of clear(), direct unit tests.
  2. web_server.Handler._do_ticket()'s action=clear_all - clears the rule book AND the AI
     desk's own book (a separate TicketBook - ai_desk.py's self.book).
  3. web_server.Handler._do_live()'s index=ALL - every index, every kind of ticket (rule, ai),
     in one call, defensive per pair.

This is real-money control surface, so every test here is about correctness under partial
failure (one bad index/ticket must not silently swallow the rest, or silently stop early) as
much as the happy path."""
import datetime as dt
import json
import os
os.environ["NBS_CRYPTO_VENUE"] = "delta"   # checks the Delta venue - the rollback path (config.CRYPTO_VENUE; Exness is exness_live_test.py)
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tickets

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
tickets.now_ist = lambda: dt.datetime(2026, 9, 29, 11, 0, 0, tzinfo=IST)

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def book():
    d = tempfile.mkdtemp()
    return tickets.TicketBook(owner=None, market="nse_index", path=os.path.join(d, "t.csv"))


def mk(index="NIFTY", option_type="CE", entry=90.0, stop=70.0, targets=(105.0, 114.0, 120.0),
       status="OPEN"):
    return {"index": index, "option_type": option_type, "strike": 25000,
            "entry_time": "10:00:00", "entry_spot": 25000.0, "entry_ts": tickets.now_ist(),
            "entry_ltp": entry, "use_premium": True, "lot_size": 65, "lots": 1, "exit_at": "T3",
            "index_targets": [], "index_sl": None,
            "premium_targets": list(targets), "premium_sl": stop,
            "hit": {"T1": False, "T2": False, "T3": False},
            "hit_time": {"T1": None, "T2": None, "T3": None},
            "sl_hit": False, "sl_hit_time": None, "time_breakeven_done": False,
            "status": status, "trade_id": f"{index}-bulktest"}


print("1. clear_all() CLEARS EVERY OPEN TICKET ACROSS EVERY INDEX AT ONCE")
b = book()
b.books["NIFTY"].trade = mk("NIFTY")
b.books["BANKNIFTY"].trade = mk("BANKNIFTY")
evs = b.clear_all()
check("two open tickets both get closed", len(evs) == 2, len(evs))
check("both marked cleared manually, not a target or a stop",
      all(e["trade"]["status"] == "CLOSED — cleared manually" for e in evs), [e["trade"]["status"] for e in evs])
check("both books are empty afterwards - a fresh ticket could be issued right away",
      b.books["NIFTY"].trade is None and b.books["BANKNIFTY"].trade is None)

print("2. AN INDEX WITH NOTHING OPEN IS SKIPPED SILENTLY, NOT AN ERROR")
b = book()
b.books["NIFTY"].trade = mk("NIFTY")
# BANKNIFTY and SENSEX have no trade at all (None) - the ordinary "nothing happened here" state
evs = b.clear_all()
check("only the one real open ticket is reported", len(evs) == 1 and evs[0]["index"] == "NIFTY", evs)

print("3. AN ALREADY-CLOSED TRADE LEFT SITTING ON THE BOOK IS ALSO SKIPPED")
b = book()
b.books["NIFTY"].trade = mk("NIFTY")
closed_leftover = mk("BANKNIFTY", status="CLOSED — T2 hit (full target reached)")
b.books["BANKNIFTY"].trade = closed_leftover
evs = b.clear_all()
check("only the genuinely OPEN one is cleared", len(evs) == 1 and evs[0]["index"] == "NIFTY", evs)
check("the already-closed leftover is untouched, not re-closed or wiped",
      b.books["BANKNIFTY"].trade is closed_leftover)

print("4. NOTHING OPEN ANYWHERE RETURNS AN EMPTY LIST, NOT AN ERROR")
b = book()
check("clear_all() on an all-quiet book returns []", b.clear_all() == [])

print("5. ONE INDEX'S PRICE LOOKUP FAILING DOES NOT STOP THE OTHERS FROM CLEARING")
b = book()
b.books["NIFTY"].trade = mk("NIFTY")
b.books["BANKNIFTY"].trade = mk("BANKNIFTY")
b.books["SENSEX"].trade = mk("SENSEX")
orig_price_for = b._price_for
def flaky_price_for(trade, rec):
    if trade["index"] == "BANKNIFTY":
        raise RuntimeError("no price available")
    return orig_price_for(trade, rec)
b._price_for = flaky_price_for
evs = b.clear_all()
check("all three still get cleared - the whole point of a bulk 'get me out' action",
      len(evs) == 3, [e["index"] for e in evs])
check("the flaky one still cleared too, just priced as None rather than being skipped",
      next(e for e in evs if e["index"] == "BANKNIFTY")["trade"]["exit"] is None)
check("every book is empty afterwards, including the one that raised",
      all(bk.trade is None for bk in b.books.values()))

print()
print("DASHBOARD BULK TEST PART 1 (clear_all) PASSED" if not fails
      else f"DASHBOARD BULK TEST PART 1 FAILED: {fails}")

print("\n6. _do_ticket action=clear_all CLEARS BOTH THE RULE BOOK AND THE AI DESK'S OWN BOOK")
import feeds
import web_server

ME = "me@example.invalid"


class FakeAI:
    def __init__(self, book):
        self.book = book


class FakeFeed:
    def __init__(self, rule_book, ai_book=None):
        self.tickets = rule_book
        self.ai = FakeAI(ai_book) if ai_book is not None else None


def do_ticket_handler(feed, user=ME, market="nse_index"):
    feeds.for_user = lambda email, mkt=None, start=True: feed
    h = object.__new__(web_server.Handler)
    out = {}
    h._send = lambda body, ctype="application/json", code=200: out.update(body=body, code=code)
    h._current_market = lambda: market
    h._current_user = lambda: user
    h._redirect = lambda path: out.update(body=json.dumps({"redirect": path}), code=302)
    return h, out


_saved_for_user = feeds.for_user
try:
    rule_book, ai_book = book(), book()
    rule_book.books["NIFTY"].trade = mk("NIFTY")
    ai_book.books["BANKNIFTY"].trade = mk("BANKNIFTY")
    feed = FakeFeed(rule_book, ai_book)
    h, out = do_ticket_handler(feed)
    h._do_ticket({"action": "clear_all"})
    d = json.loads(out["body"])
    check("reports both cleared (one rule, one AI)", d["ok"] and d["cleared"] == 2, d)
    check("the rule book's ticket is gone", rule_book.books["NIFTY"].trade is None)
    check("the AI desk's own ticket is gone too - a separate TicketBook, not missed",
          ai_book.books["BANKNIFTY"].trade is None)

    print("7. WORKS WHEN THERE IS NO AI DESK FOR THIS ACCOUNT/MARKET (feed.ai is None)")
    rule_book2 = book()
    rule_book2.books["SENSEX"].trade = mk("SENSEX")
    feed2 = FakeFeed(rule_book2, ai_book=None)
    h, out = do_ticket_handler(feed2)
    h._do_ticket({"action": "clear_all"})
    d = json.loads(out["body"])
    check("clears the one rule ticket without crashing on a missing AI desk",
          d["ok"] and d["cleared"] == 1, d)

    print("8. NOTHING OPEN ANYWHERE: cleared=0, still ok, not an error")
    feed3 = FakeFeed(book(), book())
    h, out = do_ticket_handler(feed3)
    h._do_ticket({"action": "clear_all"})
    d = json.loads(out["body"])
    check("an all-quiet market reports cleared=0", d["ok"] and d["cleared"] == 0, d)

    print("9. SIGNED OUT: REDIRECTED, NOTHING TOUCHED")
    rule_book4 = book()
    rule_book4.books["NIFTY"].trade = mk("NIFTY")
    feed4 = FakeFeed(rule_book4, book())
    h, out = do_ticket_handler(feed4, user=None)
    h._do_ticket({"action": "clear_all"})
    check("redirected to /login rather than acting", out.get("code") == 302
          and json.loads(out["body"]).get("redirect") == "/login")
    check("the open ticket is untouched", rule_book4.books["NIFTY"].trade is not None)
finally:
    feeds.for_user = _saved_for_user

print()
print("DASHBOARD BULK TEST PART 2 (_do_ticket clear_all) PASSED" if not fails
      else f"DASHBOARD BULK TEST PART 2 FAILED: {fails}")

print("\n10. _do_live index=ALL SWITCHES EVERY INDEX AND EVERY KIND OF TICKET AT ONCE")
import accounts
import user_kite


class FakeExAll:
    """Just enough of live_orders.Executor / delta_orders.Executor's interface for
    _do_live's ALL branch: set_enabled(index, on, source) and public(). Deliberately not
    the full order-simulating rig() from live_orders_test.py - the ALL branch does not
    touch order mechanics, only which set_enabled calls it makes and how it handles one
    of them failing."""
    def __init__(self, indices, fail_for=()):
        self.enabled = {i: False for i in indices}
        self.enabled_ai = {i: False for i in indices}
        self.calls = []
        self.fail_for = set(fail_for)   # {(index, source)} pairs that raise

    def set_enabled(self, index, on, source="rule"):
        self.calls.append((index, on, source))
        if (index, source) in self.fail_for:
            raise ValueError(f"could not save {index}/{source}")
        (self.enabled_ai if source == "ai" else self.enabled)[index] = on

    def public(self):
        return {"enabled": dict(self.enabled), "enabled_ai": dict(self.enabled_ai)}


class LiveFeedAll:
    def __init__(self, ex):
        self.live = ex


def all_handler(ex, user=ME, same_origin=True, market="nse_index"):
    feeds.for_user = lambda email, mkt=None, start=True: LiveFeedAll(ex)
    h = object.__new__(web_server.Handler)
    out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, code=code)
    h._current_market = lambda: market
    h._current_user = lambda: user
    h._same_origin = lambda: same_origin
    return h, out


_saved = (feeds.for_user, user_kite.token_for, web_server._state.get("mode"))
try:
    web_server._state["mode"] = "kite"
    user_kite.token_for = lambda email: "tok"

    ex = FakeExAll(("NIFTY", "BANKNIFTY", "SENSEX"))
    h, out = all_handler(ex)
    h._do_live({"index": "ALL", "on": "1"})
    d = json.loads(out["body"])
    check("index=ALL is accepted even though 'ALL' is not itself a real index", d["ok"], d)
    check("every index x every source (rule, ai) is switched on - 6 calls for 3 indices",
          len(ex.calls) == 6 and all(c[1] is True for c in ex.calls), ex.calls)
    check("both rule and ai are actually on for every index",
          all(ex.enabled.values()) and all(ex.enabled_ai.values()), (ex.enabled, ex.enabled_ai))

    print("11. index=ALL TURNING OFF NEEDS NO BROKER CONNECTION, LIKE THE SINGLE-INDEX PATH")
    user_kite.token_for = lambda email: None
    ex2 = FakeExAll(("NIFTY", "BANKNIFTY", "SENSEX"))
    ex2.enabled = {k: True for k in ex2.enabled}
    ex2.enabled_ai = {k: True for k in ex2.enabled_ai}
    h, out = all_handler(ex2)
    h._do_live({"index": "ALL", "on": "0"})
    d = json.loads(out["body"])
    check("switching everything off needs no Zerodha login, same as one index",
          d["ok"] and not any(ex2.enabled.values()) and not any(ex2.enabled_ai.values()), d)
    user_kite.token_for = lambda email: "tok"

    print("12. ONE (index, source) PAIR FAILING DOES NOT STOP THE REST OF THE MARKET")
    ex3 = FakeExAll(("NIFTY", "BANKNIFTY", "SENSEX"), fail_for={("BANKNIFTY", "ai")})
    h, out = all_handler(ex3)
    h._do_live({"index": "ALL", "on": "1"})
    d = json.loads(out["body"])
    check("all 6 calls are still attempted, not aborted at the first failure", len(ex3.calls) == 6, ex3.calls)
    check("the one failure is reported, not silently swallowed",
          not d["ok"] and "BANKNIFTY" in d["message"] and "ai" in d["message"], d)
    check("every OTHER (index, source) pair still switched on despite that one failure",
          ex3.enabled["NIFTY"] and ex3.enabled["SENSEX"] and ex3.enabled_ai["NIFTY"]
          and ex3.enabled_ai["SENSEX"] and not ex3.enabled_ai["BANKNIFTY"],
          (ex3.enabled, ex3.enabled_ai))

    print("13. index=ALL WORKS ON CRYPTO TOO - ONE INDEX (BTC), STILL BOTH SOURCES")
    import user_delta
    _saved_delta = user_delta.keys_for
    user_delta.keys_for = lambda email: {"key": "k", "secret": "s"}
    try:
        ex4 = FakeExAll(("BTC",))
        h, out = all_handler(ex4, market="crypto")
        h._do_live({"index": "ALL", "on": "1"})
        d = json.loads(out["body"])
        check("BTC's rule and AI tickets both switched on - 2 calls for 1 index",
              d["ok"] and len(ex4.calls) == 2 and ex4.enabled["BTC"] and ex4.enabled_ai["BTC"], (d, ex4.calls))
    finally:
        user_delta.keys_for = _saved_delta

    print("14. A CROSS-SITE REQUEST CANNOT SWITCH EVERYTHING ON EITHER")
    ex5 = FakeExAll(("NIFTY", "BANKNIFTY", "SENSEX"))
    h, out = all_handler(ex5, same_origin=False)
    h._do_live({"index": "ALL", "on": "1"})
    check("refused (403), nothing called", out["code"] == 403 and not ex5.calls)
finally:
    feeds.for_user, user_kite.token_for = _saved[0], _saved[1]
    web_server._state["mode"] = _saved[2]

print()
if fails:
    print(f"DASHBOARD BULK TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("DASHBOARD BULK TEST PASSED")
