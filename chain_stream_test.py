#!/usr/bin/env python3
"""The option chain streamed live: FULL-mode bookkeeping in the streamer, the
token lookup per expiry, the window around the money, what counts as live, and
the chain endpoint laying streamed values over the snapshot. Fakes only."""
import datetime as dt
import json
import os
import sys
import tempfile
import time
import types

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()           # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data_providers as dp
import feeds

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


class FakeKWS:
    MODE_LTP, MODE_QUOTE, MODE_FULL = "ltp", "quote", "full"
    def __init__(self, *a):
        self.calls = []
    def subscribe(self, toks):
        self.calls.append(("subscribe", sorted(toks)))
    def set_mode(self, mode, toks):
        self.calls.append((mode, sorted(toks)))


print("1. STREAMER: FULL MODE AND THE BOOK")
s = dp.KiteStreamer("k", "t")
s._kws = FakeKWS()
s.subscribe([11, 12], quote=True)
s.subscribe([12, 13], full=True)
check("a new FULL token is subscribed in full mode", ("subscribe", [13]) in s._kws.calls and ("full", [13]) in s._kws.calls, s._kws.calls)
check("a QUOTE token the chain also wants is stepped up to FULL", ("full", [12]) in s._kws.calls)
s._kws.calls.clear()
s.subscribe([13], quote=True)
s.subscribe([12], quote=False)
check("a later QUOTE or LTP request never steps a FULL token down", s._kws.calls == [], s._kws.calls)
now = time.time()
with s._lock:
    s._record_book([{"instrument_token": 13, "last_price": 101.5, "oi": 5000,
                     "depth": {"buy": [{"price": 101.4}], "sell": [{"price": 101.7}]}},
                    {"instrument_token": 11, "last_price": 7.0, "oi": 1}], now)
b = s.book(13)
check("FULL tick: price, OI and best bid/ask kept", b and b["ltp"] == 101.5 and b["oi"] == 5000
      and b["bid"] == 101.4 and b["ask"] == 101.7, b)
check("a token not in FULL mode has no book", s.book(11) is None)
with s._lock:
    s._record_book([{"instrument_token": 13, "last_price": 102.0}], now + 1)
b = s.book(13)
check("a price-only tick moves the price and keeps the last OI and book",
      b["ltp"] == 102.0 and b["oi"] == 5000 and b["bid"] == 101.4, b)
with s._lock:
    s._book[13]["at"] = time.time() - 60
check("older than max_age is not returned", s.book(13, max_age=20) is None and s.book(13) is not None)
with s._lock:
    s._record_book([{"instrument_token": 13, "last_price": 102.5,
                     "depth": {"buy": [{"price": 0}], "sell": [{"price": 103.0}]}}], time.time())
b = s.book(13)
check("an empty bid side (price 0) is no bid", b["bid"] is None and b["ask"] == 103.0, b)

# The real start(): its on_ticks feeds the book, and a (re)connect puts every
# token back in its own mode.
captured = {}
class FakeTicker(FakeKWS):
    def __init__(self, key, tok):
        super().__init__(); captured["t"] = self
    def connect(self, threaded=False):
        pass
fake_mod = types.ModuleType("kiteconnect"); fake_mod.KiteTicker = FakeTicker
real_mod = sys.modules.get("kiteconnect")
sys.modules["kiteconnect"] = fake_mod
try:
    s2 = dp.KiteStreamer("k", "t")
    s2.subscribe([1]); s2.subscribe([2], quote=True); s2.subscribe([3], full=True)
    check("start() with a fake ticker", s2.start())
    kt = captured["t"]; kt.calls.clear()
    kt.on_connect(kt, {})
    check("on (re)connect every token gets its own mode back",
          ("ltp", [1]) in kt.calls and ("quote", [2]) in kt.calls and ("full", [3]) in kt.calls, kt.calls)
    kt.on_ticks(kt, [{"instrument_token": 3, "last_price": 50.0, "oi": 9,
                      "depth": {"buy": [{"price": 49.9}], "sell": [{"price": 50.1}]}}])
    b = s2.book(3)
    check("ticks arriving through on_ticks land in the book", b and b["ltp"] == 50.0 and b["oi"] == 9
          and s2.price(3) == 50.0, b)
finally:
    if real_mod is not None:
        sys.modules["kiteconnect"] = real_mod


print("2. TOKENS FOR ONE EXPIRY")
p = object.__new__(dp.KiteDataProvider)
p._option_inst_cache = {"NIFTY": [
    {"name": "NIFTY", "instrument_type": "CE", "strike": 23450.0, "expiry": dt.date(2026, 9, 15), "instrument_token": 101},
    {"name": "NIFTY", "instrument_type": "PE", "strike": 23450.0, "expiry": dt.date(2026, 9, 15), "instrument_token": 102},
    {"name": "NIFTY", "instrument_type": "CE", "strike": 23450.0, "expiry": dt.date(2026, 9, 22), "instrument_token": 201}]}
mp = p.chain_tokens("NIFTY", "2026-09-15")
check("one expiry only, keyed by (strike, CE/PE)", mp == {(23450.0, "CE"): 101, (23450.0, "PE"): 102}, mp)


print("3. THE FEED PUTS THE STRIKES AROUND THE MONEY ON THE SOCKET")
LOOKUPS = []
class FakeProvider:
    def __init__(self, key, token):
        pass
    def chain_tokens(self, name, expiry):
        LOOKUPS.append((name, expiry))
        return {(float(k), kind): k * 10 + (1 if kind == "CE" else 2)
                for k in range(22000, 25050, 50) for kind in ("CE", "PE")}
feeds.KiteDataProvider = FakeProvider
feeds.user_kite.token_for = lambda email: "tok"
OPEN = {"v": True}
feeds.is_market_open = lambda now, ref=None: OPEN["v"]

class FakeStreamer:
    def __init__(self):
        self.subs, self.books = [], {}
    def subscribe(self, toks, quote=False, full=False):
        self.subs.append((sorted(toks), full))
    def book(self, tok, max_age=None):
        return self.books.get(tok)

f = feeds.Feed("t:chain", "test@example.invalid", "nse_index")
st = FakeStreamer(); f.streamer = st
f.base_oi["NIFTY"] = {"expiry": "2026-09-15", "spot": 23461.0,
                      "strikes": [{"strike": float(k)} for k in range(22000, 25050, 50)]}
f._subscribe_chain("NIFTY")
toks, full = st.subs[0]
strikes = sorted({t // 10 for t in toks})
check("25 strikes, calls and puts, in FULL mode", len(toks) == 50 and full, (len(toks), full))
check("centred on the money: 23,450 plus and minus 12 strikes of 50",
      strikes[0] == 22850 and strikes[-1] == 24050, (strikes[0], strikes[-1]))
f._subscribe_chain("NIFTY")
check("nothing new on the next loop", len(st.subs) == 1 and len(LOOKUPS) == 1, (len(st.subs), len(LOOKUPS)))
f.base_oi["NIFTY"]["spot"] = 23620.0
f._subscribe_chain("NIFTY")
added = sorted({t // 10 for t in st.subs[-1][0]}) if len(st.subs) > 1 else None
check("spot moves three strikes: only the new strikes are added", added == [24100, 24150, 24200], added)
check("no second instrument lookup for the same expiry", len(LOOKUPS) == 1)
f.base_oi["NIFTY"]["expiry"] = "2026-09-22"
f._subscribe_chain("NIFTY")
check("a new expiry is looked up once", len(LOOKUPS) == 2 and LOOKUPS[-1] == ("NIFTY", "2026-09-22"))
st2 = FakeStreamer(); f.streamer = st2
f._subscribe_chain("NIFTY")
check("a rebuilt socket is given the chain again", len(st2.subs) == 1 and len(st2.subs[0][0]) == 50, st2.subs)

class Boom(FakeProvider):
    def chain_tokens(self, name, expiry):
        raise RuntimeError("instrument dump timed out")
feeds.KiteDataProvider = Boom
f.base_oi["SENSEX"] = {"expiry": "2026-09-17", "spot": 75000.0, "strikes": [{"strike": 75000.0}]}
f._subscribe_chain("SENSEX"); t1 = f._chain_tried.get("SENSEX")
f._subscribe_chain("SENSEX")
check("a failed lookup waits a minute before trying again",
      t1 is not None and f._chain_tried["SENSEX"] == t1 and "SENSEX" not in f.chain_toks)
feeds.KiteDataProvider = FakeProvider

g = feeds.Feed("t:crypto", "test@example.invalid", "crypto")
g.streamer = feeds._NO_STREAM
g.base_oi["BTC"] = {"expiry": "2026-10-30", "spot": 77000.0, "strikes": [{"strike": 77000.0}]}
n0 = len(LOOKUPS); g._subscribe_chain("BTC")
check("crypto, with no Zerodha socket, is left alone", len(LOOKUPS) == n0 and not g.chain_toks)


print("4. WHAT COUNTS AS LIVE")
tok = f.chain_toks["NIFTY"]["map"][(23450.0, "CE")]
st2.books[tok] = {"ltp": 61.2, "oi": 13500000, "bid": 61.1, "ask": 61.3, "at": time.time()}
live = f.chain_live("NIFTY")
check("a strike with a fresh tick is live", live.get((23450.0, "CE"), {}).get("ltp") == 61.2 and len(live) == 1, live)
f.streamer = st
check("ticks from a socket that has been replaced are not live", f.chain_live("NIFTY") == {})
f.streamer = st2
OPEN["v"] = False
check("after the bell nothing is called live", f.chain_live("NIFTY") == {})
OPEN["v"] = True


print("5. THE CHAIN ENDPOINT")
import web_server as ws_mod
snap = {"expiry": "2026-09-15", "spot": 23461.0, "pcr": 0.86, "max_pain": 23450,
        "top_call_oi_strike": 23500, "top_put_oi_strike": 23300,
        "strikes": [{"strike": 23450.0, "call_ltp": 60.3, "call_bid": 60.2, "call_ask": 60.5, "call_oi": 13000000,
                     "put_ltp": 54.7, "put_bid": 54.6, "put_ask": 54.9, "put_oi": 14000000},
                    {"strike": 23500.0, "call_ltp": 35.0, "call_bid": 34.9, "call_ask": 35.2, "call_oi": 20000000,
                     "put_ltp": 80.0, "put_bid": 79.8, "put_ask": 80.3, "put_oi": 9000000}]}
class FakeFeed:
    def chain(self, name):
        return snap
    def snapshot(self):
        return {"indices": {"NIFTY": {"strike": 23450, "option_type": "CE"}}}
    def chain_live(self, name):
        return {(23450.0, "CE"): {"ltp": 61.25, "oi": 13200000, "bid": 61.2, "ask": 61.3, "at": time.time()}}
ws_mod.feeds.for_user = lambda user, market: FakeFeed()
out = {}
class FakeHandler:
    def _current_market(self):
        return "nse_index"
    def _send(self, body, ctype):
        out["body"] = json.loads(body)
ws_mod.Handler._api_chain(FakeHandler(), "u@example.invalid", {"index": ["NIFTY"]})
d = out["body"]
rows = {r["strike"]: r for r in d["rows"]}
ce, pe = rows[23450.0]["ce"], rows[23450.0]["pe"]
check("streamed strike: price, book and OI from the tick", ce["ltp"] == 61.25 and ce["bid"] == 61.2
      and ce["ask"] == 61.3 and ce["oi"] == 13200000 and ce["live"], ce)
check("its spread from the streamed book", ce["spread"] == round((61.3 - 61.2) / ((61.3 + 61.2) / 2) * 100, 2), ce["spread"])
check("OI change measured against the day's first snapshot", ce["oi_chg"] == 200000, ce["oi_chg"])
check("a contract with no tick keeps the snapshot", pe["ltp"] == 54.7 and not pe["live"]
      and rows[23500.0]["ce"]["ltp"] == 35.0, pe)
check("payload counts live contracts and stamps the last tick", d["live"] == 1 and d["live_at"]
      and len(d["live_at"]) == 8, (d["live"], d["live_at"]))
check("PCR, max pain and the walls are still the snapshot's", d["pcr"] == 0.86 and d["call_wall"] == 23500)
page = ws_mod.PAGE
check("the page asks for the chain from the fast price poll while its tab is open",
      'if(TAB === "chain") chainFetch();' in page and "CHAIN_LIVE ? 1000 : 20000" in page)

print("CHAIN STREAM TEST PASSED" if not fails else f"CHAIN STREAM TEST FAILED: {fails}")
