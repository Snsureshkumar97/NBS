#!/usr/bin/env python3
"""The contract chart: one strike's own candles, and the button that opens it.

Fakes only - no Zerodha call and no socket. The handler is built bare and its
_send captured, which is what a browser would receive. What matters here is
that the endpoint validates what it is given, asks the provider for the right
contract and interval, never asks twice inside the cache window, carries an
open ticket's own levels, and says plainly when there is nothing to draw.
"""
import json
import os
import sys
import tempfile

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


TS = pd.date_range("2026-09-16 11:45", periods=3, freq="5min", tz="Asia/Kolkata")
BARS = pd.DataFrame({"ts": TS, "open": [105.85, 107.85, 111.55],
                     "high": [108.9, 112.0, 111.9], "low": [100.55, 106.65, 108.7],
                     "close": [107.65, 111.55, 109.3], "volume": [1320475, 1570465, 165295]})


class FakeProvider:
    def __init__(self, token=14586114, boom=None, rows=BARS):
        self.token, self.boom, self.rows = token, boom, rows
        self.asked, self.fetched = [], []

    def option_token(self, index_key, strike, option_type, expiry=None):
        self.asked.append((index_key, strike, option_type, expiry))
        return self.token

    def candles_for_token(self, token, interval="day", days=90):
        self.fetched.append((token, interval, days))
        if self.boom:
            raise RuntimeError(self.boom)
        return self.rows


class FakeBook:
    def __init__(self, ticket=None):
        self.ticket = ticket
    def public(self, name):
        return {"ticket": self.ticket, "wait": None}


class FakeFeed:
    def __init__(self, provider, ticket=None):
        self.prov, self.tickets = provider, FakeBook(ticket)
    def _provider(self):
        return (self.prov, "ok", "")


def call(rest, qs=None, provider=None, ticket=None, market="nse_index"):
    """Run the endpoint and return (payload, status)."""
    prov = provider if provider is not None else FakeProvider()
    feeds.for_user = lambda email, mkt=None, start=True: FakeFeed(prov, ticket)
    h = object.__new__(web_server.Handler)
    out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, ctype=ctype, code=code)
    h._current_market = lambda: market
    h._option_candles("someone@example.com", rest, qs or {})
    return json.loads(out["body"]), out.get("code", 200), prov


_real_for_user = feeds.for_user
web_server.Handler._OPT_CACHE.clear()

print("1. WHAT IT REFUSES")
p, code, _ = call("NIFTY/23100")
check("a request with no option type is refused", code == 400 and p["error"], (code, p.get("error")))
p, code, _ = call("NIFTY/not-a-number/PE")
check("a strike that is not a number is refused", code == 400, (code, p.get("error")))
p, code, _ = call("NIFTY/23100/XX")
check("something that is neither CE nor PE is refused", code == 400, (code, p.get("error")))
p, code, prov = call("BTC/76000/PE", market="crypto")
check("crypto says so instead of drawing an empty box",
      p["candles"] == [] and "Indian indices" in (p.get("error") or ""), p.get("error"))
check("...and Zerodha is never asked for a Deribit contract", prov.asked == [], prov.asked)

print("2. THE CONTRACT'S OWN CANDLES")
web_server.Handler._OPT_CACHE.clear()
p, code, prov = call("NIFTY/23100/PE", {"tf": ["5m"], "expiry": ["2026-09-24"]})
check("the right contract is looked up",
      prov.asked == [("NIFTY", 23100.0, "PE", "2026-09-24")], prov.asked)
check("five-minute candles are asked for", prov.fetched and prov.fetched[0][1] == "5minute", prov.fetched)
check("the instrument token is used, not the strike", prov.fetched[0][0] == 14586114, prov.fetched)
check("three candles come back as [time, o, h, l, c, v]",
      len(p["candles"]) == 3 and len(p["candles"][0]) == 6, p["candles"][:1])
check("the last close is the contract's premium", p["candles"][-1][4] == 109.3, p["candles"][-1])
check("the time is ISO, with the Indian offset",
      p["candles"][0][0].startswith("2026-09-16T11:45") and "+05:30" in p["candles"][0][0],
      p["candles"][0][0])
check("the contract is echoed back for the title",
      (p["index"], p["strike"], p["option_type"], p["expiry"]) == ("NIFTY", 23100.0, "PE", "2026-09-24"),
      (p["index"], p["strike"], p["option_type"], p["expiry"]))

print("3. IT DOES NOT PESTER ZERODHA")
web_server.Handler._OPT_CACHE.clear()
_, _, prov = call("NIFTY/23100/PE", {"tf": ["5m"]})
feeds.for_user = lambda email, mkt=None, start=True: FakeFeed(prov)
h = object.__new__(web_server.Handler)
h._send = lambda body, ctype="text/html", code=200: None
h._current_market = lambda: "nse_index"
h._option_candles("someone@example.com", "NIFTY/23100/PE", {"tf": ["5m"]})
check("asking again inside the cache window does not re-fetch", len(prov.fetched) == 1, prov.fetched)
h._option_candles("someone@example.com", "NIFTY/23100/PE", {"tf": ["15m"]})
check("a different timeframe does fetch, at that interval",
      len(prov.fetched) == 2 and prov.fetched[1][1] == "15minute", prov.fetched)

print("4. AN OPEN TICKET'S OWN LEVELS")
web_server.Handler._OPT_CACHE.clear()
TICKET = {"open": True, "strike": 23100, "option_type": "PE", "tracked_on": "premium",
          "targets": [120.0, 135.0, 150.0], "stop": 80.0, "entry": 105.85}
p, _, _ = call("NIFTY/23100/PE", {"tf": ["5m"]}, ticket=TICKET)
check("the ticket's frozen premium levels ride along",
      (p.get("levels") or {}).get("t2") == 135.0 and p["levels"]["stop"] == 80.0
      and p["levels"]["entry"] == 105.85, p.get("levels"))
web_server.Handler._OPT_CACHE.clear()
p, _, _ = call("NIFTY/23200/PE", {"tf": ["5m"]}, ticket=TICKET)
check("a different strike does not borrow them", "levels" not in p, p.get("levels"))
web_server.Handler._OPT_CACHE.clear()
p, _, _ = call("NIFTY/23100/PE", {"tf": ["5m"]},
               ticket=dict(TICKET, tracked_on="index"))
check("nor does a ticket tracked on the index - those levels are index points",
      "levels" not in p, p.get("levels"))

print("5. WHEN IT CANNOT DRAW, IT SAYS SO")
web_server.Handler._OPT_CACHE.clear()
p, code, _ = call("NIFTY/23100/PE", provider=FakeProvider(token=None))
check("no such contract: a sentence, not a crash",
      p["candles"] == [] and "No contract" in p["error"], p.get("error"))
web_server.Handler._OPT_CACHE.clear()
p, code, _ = call("NIFTY/23100/PE", provider=FakeProvider(boom="rate limit"))
check("Zerodha refusing history: the reason is passed through",
      p["candles"] == [] and "rate limit" in p["error"], p.get("error"))
web_server.Handler._OPT_CACHE.clear()
p, code, _ = call("NIFTY/23100/PE", provider=FakeProvider(rows=pd.DataFrame()))
check("no rows: an empty chart, not an error", p["candles"] == [] and code == 200)

print("6. THE BUTTON AND THE BOX ON THE PAGE")
SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()
check("the endpoint is routed", '/api/option_candles/' in SRC and 'self._option_candles(' in SRC)
check("the signal card has a View chart button", 'id="ocopen"' in SRC and ">View chart<" in SRC)
check("the open ticket's contract line has one too", "ocOpenFrom(this)" in SRC)
check("the popup exists with its own canvas", 'class="oc" id="oc"' in SRC and 'id="occv"' in SRC)
check("it closes on Escape", 'e.key === "Escape" && OC.open' in SRC)
check("it refreshes itself while open", "setInterval(ocFetch" in SRC)
check("its tags use the readable-text helper", "onColour(" in SRC.split("function ocDraw()")[1][:4000])
# A fixed overlay inside a card would be positioned against the card, because
# the 3D layer transforms cards - so the box must sit outside .wrap.
check("the popup sits outside the transformed .wrap",
      SRC.index('class="oc" id="oc"') < SRC.index('<aside class="side"'))

feeds.for_user = _real_for_user
print("STRIKE CHART TEST PASSED" if not fails else f"STRIKE CHART TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
