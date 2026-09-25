#!/usr/bin/env python3
"""The markets strip: India's, the crypto screen's, and what /api/markets hands each.

The user (25 Sep 2026): "i dont see a ticker of market scrolling in crypto market". The strip was hidden on
the crypto screen (Indian indices are not context for a coin), so the crypto screen has its own: the coins,
then the world block. The rule this pins: the coins are their OWN group, so the Indian screen's strip - and the
Indian AI desk's world block - which ask for "everything" never grow coins, and the crypto screen asks for
"crypto,world". Also: a coin worth 9 cents moves in fractions of a cent, so a change is not rounded to 2 places.

A real server on a random local port, Yahoo replaced by a fake; no network, no account.
"""
import http.client
import json
import os
import sys
import tempfile
import threading

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import market_ticker
import web_server

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. THE LIST")
by = {}
for t, label, dp, g in market_ticker.MARKETS:
    by.setdefault(g, []).append((t, label, dp))
check("there is a crypto block, coins first: BTC then ETH, SOL, XRP, BNB, DOGE",
      [l for _, l, _ in by.get("crypto", [])] == ["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "BNB/USD", "DOGE/USD"],
      [l for _, l, _ in by.get("crypto", [])])
check("India and the world are as they were (19 and 11 markets, BTC/USD still in the world block Home reads)",
      len(by["india"]) == 19 and len(by["world"]) == 11 and ("BTC-USD", "BTC/USD", 0) in by["world"],
      (len(by["india"]), len(by["world"])))
check("a coin worth cents shows enough decimals (XRP and DOGE at 4)", dict((l, d) for _, l, d in by["crypto"])["XRP/USD"] == 4
      and dict((l, d) for _, l, d in by["crypto"])["DOGE/USD"] == 4)

print("2. A CHANGE IS NOT ROUNDED AWAY")
class FakeResp:
    def __init__(self, price, prev): self.p, self.q = price, prev
    def raise_for_status(self): pass
    def json(self): return {"chart": {"result": [{"meta": {"regularMarketPrice": self.p, "chartPreviousClose": self.q}}]}}
real_get = market_ticker.requests.get
market_ticker.requests.get = lambda *a, **k: FakeResp(0.0960, 0.0996)
r = market_ticker._one("DOGE-USD", "DOGE/USD", 4)
check("DOGE down 0.0036 says so, not -0.0", r["change"] == -0.0036, r)
market_ticker.requests.get = lambda *a, **k: FakeResp(24000.126, 23900.0)
r = market_ticker._one("^NSEI", "NIFTY 50", 2)
check("an index is still rounded to 2 places, as it was", r["change"] == 100.13 and r["price"] == 24000.13, r)
market_ticker.requests.get = real_get

print("3. WHAT /api/markets HANDS EACH")
market_ticker.rows = lambda force=False: [
    {"label": "NIFTY 50", "price": 1, "change": 0, "pct": 0, "dp": 2, "group": "india"},
    {"label": "NIKKEI", "price": 2, "change": 0, "pct": 0, "dp": 0, "group": "world"},
    {"label": "BTC/USD", "price": 3, "change": 0, "pct": 0, "dp": 0, "group": "world"},
    {"label": "BTC/USD", "price": 3, "change": 0, "pct": 0, "dp": 0, "group": "crypto"},
    {"label": "ETH/USD", "price": 4, "change": 0, "pct": 0, "dp": 2, "group": "crypto"}]
srv = web_server.Server(("127.0.0.1", 0), web_server.Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
def get(q):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", "/api/markets" + q)
    r = c.getresponse()
    body = json.loads(r.read())
    c.close()
    return r.status, [(x["group"], x["label"]) for x in body["rows"]]
st, rows = get("")
check("no group (India's strip, the marketing pages): India and the world, no coins",
      st == 200 and [g for g, _ in rows] == ["india", "world", "world"] and all(g != "crypto" for g, _ in rows), rows)
st, rows = get("?group=world")
check("group=world (Home's block): the world alone", [g for g, _ in rows] == ["world", "world"], rows)
st, rows = get("?group=crypto,world")
check("group=crypto,world (the crypto screen's strip): the coins FIRST, then the world, never India",
      [g for g, _ in rows] == ["crypto", "crypto", "world", "world"] and ("crypto", "ETH/USD") in rows, rows)
st, rows = get("?group=world,crypto")
check("...and the order is the one asked for", [g for g, _ in rows] == ["world", "world", "crypto", "crypto"], rows)
st, rows = get("?group=world,world")
check("a group named twice is answered once", [g for g, _ in rows] == ["world", "world"], rows)
st, rows = get("?group=crypto")
check("group=crypto alone: only the coins", [g for g, _ in rows] == ["crypto", "crypto"], rows)
st, rows = get("?group=,,world,")
check("stray commas are ignored", [g for g, _ in rows] == ["world", "world"], rows)
srv.shutdown()

print("4. THE INDIAN AI DESK'S WORLD BLOCK")
import bot_data
class Ctx:  # what _world reads
    market = "nse"
c = Ctx(); out = bot_data._world(c, {})
check("the Indian desk sees India and the world, no coins", all(r["group"] != "crypto" for r in out["rows"]) and len(out["rows"]) == 3, out)
c.market = "crypto"; out = bot_data._world(c, {})
check("the crypto desk still sees the world block alone (as before)", [r["group"] for r in out["rows"]] == ["world", "world"], out)

print("MARKET STRIP TEST PASSED" if not fails else f"MARKET STRIP TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
