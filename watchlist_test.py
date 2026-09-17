#!/usr/bin/env python3
"""The watchlist: what it keeps, the prices it shows, and who may change it.

Fakes only - no broker, no socket, a temporary log folder. The things that
matter: the list is per account AND per market, one contract is one entry
however its strike is written, an expired contract says so instead of quietly
showing next week's price, crypto marks are converted to dollars, and a
request from another site cannot change anyone's list.
"""
import datetime as dt
import json
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()      # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feeds
import watchlist as wl
import web_server

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


ME, OTHER = "me@example.invalid", "other@example.invalid"

print("1. WHAT IT KEEPS")
iid, added = wl.add(ME, "nse_index", "nifty", "2026-09-22", "23100", "pe", price="99.15")
check("a contract is added, index and side normalised", added and iid == "NIFTY|2026-09-22|23100|PE", iid)
iid2, added2 = wl.add(ME, "nse_index", "NIFTY", "2026-09-22", 23100.0, "PE")
check("23100 and 23100.0 are the same contract - not added twice", not added2 and iid2 == iid)
check("the price when it was added is kept", wl.load(ME, "nse_index")[0]["added_price"] == 99.15)
check("another account has its own, empty list", wl.load(OTHER, "nse_index") == [])
check("the crypto list is separate from the Indian one", wl.load(ME, "crypto") == [])
for bad, why in ((("BTC", "2026-09-25", 77000, "CE"), "an index from the other market"),
                 (("NIFTY", "2026-09-22", "abc", "CE"), "a strike that is not a number"),
                 (("NIFTY", "2026-09-22", 23100, "XX"), "neither CE nor PE"),
                 (("NIFTY", "22 Sep<script>", 23100, "CE"), "an expiry with markup in it")):
    try:
        wl.add(ME, "nse_index", *bad); ok = False
    except ValueError:
        ok = True
    check(f"refused: {why}", ok)
check("the id the page builds matches the server's", wl.strike_text(23100.5) == "23100.5" and wl.strike_text(23100.0) == "23100")
for k in range(1, wl.MAX_ITEMS):
    wl.add(ME, "nse_index", "NIFTY", "2026-09-22", 20000 + k * 50, "CE")
try:
    wl.add(ME, "nse_index", "NIFTY", "2026-09-22", 99999, "CE"); full = False
except ValueError as exc:
    full = "remove one" in str(exc)
check(f"it stops at {wl.MAX_ITEMS} and says why", full)
check("removing works, and only once", wl.remove(ME, "nse_index", iid) and not wl.remove(ME, "nse_index", iid))

print("2. INDIAN PRICES")
class FakeKite:
    def __init__(self): self.calls = []
    def quote(self, toks):
        self.calls.append(list(toks))
        return {str(t): {"last_price": 101.5, "oi": 4245085,
                         "depth": {"buy": [{"price": 101.0}], "sell": [{"price": 102.0}]}} for t in toks}
class FakeKiteProvider:
    def __init__(self): self.kite = FakeKite()
    def _all_option_instruments(self, idx):
        return "NFO", [{"expiry": dt.date(2026, 9, 22), "strike": 23100.0, "instrument_type": "PE", "instrument_token": 14586114},
                       {"expiry": dt.date(2026, 9, 29), "strike": 23100.0, "instrument_type": "PE", "instrument_token": 99999999}]
items = [{"id": "NIFTY|2026-09-22|23100|PE", "index": "NIFTY", "expiry": "2026-09-22", "strike": 23100.0, "side": "PE"},
         {"id": "NIFTY|2026-09-15|23100|PE", "index": "NIFTY", "expiry": "2026-09-15", "strike": 23100.0, "side": "PE"}]
kp = FakeKiteProvider()
q = wl.quotes(kp, items, "nse_index")
live = q["NIFTY|2026-09-22|23100|PE"]
check("last, bid and ask come back", (live["last"], live["bid"], live["ask"]) == (101.5, 101.0, 102.0), live)
check("the spread is worked out", live["spread_pct"] == round(1 / 101.5 * 100, 2), live["spread_pct"])
check("an expired contract says so - it does not borrow next week's price",
      "expired" in q["NIFTY|2026-09-15|23100|PE"].get("error", ""), q["NIFTY|2026-09-15|23100|PE"])
check("every live contract is priced in one broker call", kp.kite.calls == [[14586114]], kp.kite.calls)

print("3. CRYPTO PRICES")
class FakeDeribit:
    def _chain_rows(self, idx):
        return [{"expiry": "25SEP26", "strike": 77000, "kind": "C", "oi": 12.0,
                 "mark_coin": 0.03, "bid_coin": 0.029, "ask_coin": 0.031}]
    def spot(self, idx): return 76000.0
q = wl.quotes(FakeDeribit(), [{"id": "BTC|2026-09-25|77000|CE", "index": "BTC", "expiry": "2026-09-25",
                                "strike": 77000.0, "side": "CE"}], "crypto")["BTC|2026-09-25|77000|CE"]
check("coin-quoted marks are converted to dollars at the index", (q["last"], q["bid"], q["ask"]) == (2280.0, 2204.0, 2356.0), q)

print("4. THE ENDPOINTS")
class FakeFeed:
    def _provider(self): return (FakeKiteProvider(), "ok", "")
feeds.for_user = lambda email, market=None, start=True: FakeFeed()


def handler(user=ME, same_origin=True, market="nse_index"):
    h = object.__new__(web_server.Handler)
    out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, code=code)
    h._current_market = lambda: market
    h._current_user = lambda: user
    h._same_origin = lambda: same_origin
    return h, out


web_server.Handler._WATCH_CACHE.clear()
h, out = handler()
h._do_watchlist({"action": "add", "index": "NIFTY", "expiry": "2026-09-22", "strike": "23100", "side": "PE", "price": "99.15"})
check("POST add is accepted", json.loads(out["body"])["ok"], out["body"])
h, out = handler(same_origin=False)
h._do_watchlist({"action": "remove", "id": "NIFTY|2026-09-22|23100|PE"})
check("a request from another site is refused", out["code"] == 403, out.get("code"))
h, out = handler(user=None)
h._do_watchlist({"action": "add", "index": "NIFTY", "expiry": "2026-09-22", "strike": "1", "side": "CE"})
check("signed out: refused", out["code"] == 401)
h, out = handler()
h._api_watchlist(ME)
d = json.loads(out["body"])
row = next(x for x in d["items"] if x["id"] == "NIFTY|2026-09-22|23100|PE")
check("GET returns the list with a live price on each contract", row["quote"].get("last") == 101.5, row["quote"])
check("...and the currency and the cap", d["currency"] == "INR" and d["max"] == wl.MAX_ITEMS)
h, out = handler()
h._do_watchlist({"action": "remove", "id": "NIFTY|2026-09-22|23100|PE"})
check("POST remove works", json.loads(out["body"])["ok"])

print("5. ON THE PAGE")
SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()
check("a Watchlist section and sidebar entry", '"watchlist"' in SRC and 'data-tab="watchlist"' in SRC
      and 'data-pane="watchlist"' in SRC)
check("a star beside every price in the chain", 'class="wstar${watched ? " on" : ""}"' in SRC)
check("the page and the server build the same id", "const wlId = (k, exp, strike, side) => `${k}|${exp}|${String(Number(strike))}|${side}`;" in SRC)
check("it refreshes only while the watchlist is open", 'if(TAB === "watchlist" && !document.hidden) watchFetch();' in SRC)
check("opening the chain loads the list, so its stars are right", "chainFetch(true); oiFetch(); watchFetch(true);" in SRC)
check("each row can open that contract's chart", 'onclick="ocOpenFrom(this)">View chart</button><button class="lbtn ocbtn wrm"' in SRC)

print("WATCHLIST TEST PASSED" if not fails else f"WATCHLIST TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
