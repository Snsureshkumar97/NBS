#!/usr/bin/env python3
"""The strike chart's live side: one contract's quote off the feed's own socket,
the endpoint that serves it once a second, the vendored TradingView Lightweight
Charts library, and the merge logic that moves the forming candle. Fakes only -
no venue, no socket, nothing reaches a real account."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()           # nothing touches the real logs
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config
config.ENABLE_CRYPTO = True
import feeds
import web_server

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


OPEN = {"v": True}
feeds.is_market_open = lambda now, ref=None: OPEN["v"]


class FakeStreamer:
    def __init__(self):
        self.books = {}
    def subscribe(self, toks, quote=False, full=False):
        pass
    def book(self, tok, max_age=None):
        b = self.books.get(tok)
        if b and max_age is not None and time.time() - b["at"] > max_age:
            return None
        return dict(b) if b else None
    def price(self, tok):
        b = self.books.get(tok)
        return b["ltp"] if b else None


print("1. ONE CONTRACT'S QUOTE OFF THE INDIAN FEED'S SOCKET")
f = feeds.Feed("t:live", "test@example.invalid", "nse_index")
st = FakeStreamer(); f.streamer = st
now = time.time()
f.chain_toks["NIFTY"] = {"expiry": "2026-09-22", "map": {(23400.0, "CE"): 5, (23400.0, "PE"): 6}, "streamed": {5, 6}, "on": st}
st.books[5] = {"ltp": 101.5, "bid": 101.4, "ask": 101.7, "oi": 5000, "at": now}
q = f.contract_quote("NIFTY", 23400, "CE", "2026-09-22")
check("a streamed strike on the chain's expiry: price, bid, ask, OI and when", q and q["ltp"] == 101.5 and q["bid"] == 101.4
      and q["ask"] == 101.7 and q["oi"] == 5000 and q["at"] == now and q["source"] == "chain", q)
check("the side is CE/PE or C/P, the strike a float or a string", f.contract_quote("NIFTY", "23400.0", "call", "2026-09-22")["ltp"] == 101.5)
check("an expiry given with a time on it still matches", f.contract_quote("NIFTY", 23400, "CE", "2026-09-22T00:00:00")["ltp"] == 101.5)
check("no expiry named: the chain's own", f.contract_quote("NIFTY", 23400, "CE", None)["ltp"] == 101.5)
check("a strike that is not streamed: None, never a guess", f.contract_quote("NIFTY", 23450, "CE", "2026-09-22") is None)
check("a different expiry than the chain streams: None", f.contract_quote("NIFTY", 23400, "CE", "2026-09-29") is None)
check("a strike that is not a number: None", f.contract_quote("NIFTY", "x", "CE", None) is None)
check("the wrong side is a different contract (no put streamed here)", st.books.get(6) is None and f.contract_quote("NIFTY", 23400, "PE", "2026-09-22") is None)
st.books[5]["at"] = time.time() - 60
check("a tick older than the stream's freshness limit is not a quote", f.contract_quote("NIFTY", 23400, "CE", "2026-09-22") is None)
st.books[5]["at"] = time.time()
OPEN["v"] = False
check("after the bell the sockets keep sending packets that no longer move: no quote", f.contract_quote("NIFTY", 23400, "CE", "2026-09-22") is None)
OPEN["v"] = True

print("2. AN OPEN TICKET'S OWN CONTRACT STREAMS EVEN AFTER IT LEAVES THE CHAIN'S WINDOW")
f.tickets.books = {"NIFTY": types.SimpleNamespace(trade={"status": "OPEN", "strike": 23500, "option_type": "CE", "expiry": "2026-09-15"})}
f.opt_tokens["NIFTY"] = 9
st.books[9] = {"ltp": 55.0, "bid": 54.9, "ask": 55.2, "oi": 900, "at": time.time()}
q = f.contract_quote("NIFTY", 23500, "CE", "2026-09-15")
check("the ticket's contract: price from its own subscription, marked as the ticket's", q and q["ltp"] == 55.0 and q["source"] == "ticket" and q["bid"] == 54.9, q)
check("...only for THAT contract: another strike or expiry is not borrowed",
      f.contract_quote("NIFTY", 23500, "PE", "2026-09-15") is None and f.contract_quote("NIFTY", 23500, "CE", "2026-09-22") is None
      and f.contract_quote("NIFTY", 23600, "CE", "2026-09-15") is None)
f.tickets.books = {"NIFTY": types.SimpleNamespace(trade={"status": "CLOSED", "strike": 23500, "option_type": "CE", "expiry": "2026-09-15"})}
check("a closed ticket's contract is no longer streamed for it", f.contract_quote("NIFTY", 23500, "CE", "2026-09-15") is None)
f.streamer = None
check("no socket at all: None, no error", f.contract_quote("NIFTY", 23400, "CE", "2026-09-22") is None)

print("3. BITCOIN, OFF DELTA'S SOCKET, IN DOLLARS")
class FakeDelta:
    QUOTES_IN_COIN = False
    def __init__(self):
        self.books, self.idx = {}, 80400.0
    def book(self, inst, max_age=None):
        b = self.books.get(inst)
        if b and max_age is not None and time.time() - b["at"] > max_age:
            return None
        return dict(b) if b else None
    def index_price(self, name):
        return self.idx
    def mark_usd(self, inst, index=None):
        b = self.books.get(inst)
        return b["mark"] if b else None
g = feeds.Feed("t:live-c", "test@example.invalid", "crypto")
ds = FakeDelta(); g.streamer = feeds._NO_STREAM; g.dstream = ds
inst = "C-BTC-80000-250925"
g.chain_toks["BTC"] = {"expiry": "2026-09-25", "map": {(80000.0, "CE"): inst}, "streamed": {inst}, "on": ds}
ds.books[inst] = {"mark": 1510.25, "bid": 1500.0, "ask": 1520.0, "last": 1490.0, "oi": 16.4, "at": time.time()}
q = g.contract_quote("BTC", 80000, "CE", "2026-09-25")
check("Delta quotes dollars: nothing multiplied, and the mark (not a stale last trade) is the price",
      q and q["ltp"] == 1510.25 and q["bid"] == 1500.0 and q["ask"] == 1520.0 and q["oi"] == 16.4, q)
g.chain_toks["BTC"]["expiry"] = "2026-10-02"
g.tickets.books = {"BTC": types.SimpleNamespace(trade={"status": "OPEN", "strike": 80000, "option_type": "CE", "expiry": "2026-09-25"})}
g.opt_tokens["BTC"] = inst
q = g.contract_quote("BTC", 80000, "CE", "2026-09-25")
check("the ticket's Bitcoin contract off the same socket once the chain has moved to another expiry",
      q and q["ltp"] == 1510.25 and q["source"] == "ticket", q)

print("4. THE ENDPOINT THE CHART POLLS EVERY SECOND")
class Stub:
    def __init__(self, q): self.q = q
    def contract_quote(self, name, strike, side, expiry=None):
        self.asked = (name, strike, side, expiry)
        return self.q
def handler(market="nse_index"):
    h = object.__new__(web_server.Handler)
    out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, code=code)
    h._current_market = lambda: market
    return h, out
seen = []
real = feeds.for_user
def fake_for_user(user, market=None, start=True):
    seen.append(start)
    return STUB[0]
STUB = [Stub({"ltp": 109.8, "bid": 109.7, "ask": 110.0, "oi": 5000, "at": time.time() - 2, "source": "chain"})]
feeds.for_user = fake_for_user
try:
    h, out = handler()
    h._option_tick("me@example.invalid", "NIFTY/23100/PE", {"expiry": ["2026-09-24"]})
    d = json.loads(out["body"])
    check("a live quote: price, bid, ask, OI, its age, live, the server's own clock (the candle bucket must agree with the venue's), its source",
          d["ltp"] == 109.8 and d["live"] is True and 1 <= d["age"] <= 4 and isinstance(d["t"], int) and d["source"] == "chain"
          and STUB[0].asked == ("NIFTY", 23100.0, "PE", "2026-09-24"), d)
    check("it never starts a feed (start=False) - polling must not bring a stopped feed to life", seen == [False], seen)
    STUB[0] = Stub({"ltp": 109.8, "at": time.time() - 40, "source": "chain"})
    h, out = handler(); h._option_tick("me@example.invalid", "NIFTY/23100/PE", {})
    d = json.loads(out["body"])
    check("a price 40 s old is returned but not live - the chart must not move its candle on it", d["ltp"] == 109.8 and d["live"] is False)
    STUB[0] = Stub(None)
    h, out = handler(); h._option_tick("me@example.invalid", "NIFTY/23100/PE", {})
    d = json.loads(out["body"])
    check("not streamed: {live: false} - no invented price", d.get("live") is False and "ltp" not in d and isinstance(d.get("t"), int), d)
    feeds.for_user = lambda user, market=None, start=True: None
    h, out = handler(); h._option_tick("me@example.invalid", "NIFTY/23100/PE", {})
    check("no feed running at all: {live: false}", json.loads(out["body"]).get("live") is False and out["code"] == 200)
    h, out = handler(); h._option_tick("me@example.invalid", "NIFTY/23100", {})
    check("no option type: 400", out["code"] == 400)
    h, out = handler(); h._option_tick("me@example.invalid", "NIFTY/abc/CE", {})
    check("a strike that is not a number: 400", out["code"] == 400)
    h, out = handler(); h._option_tick("me@example.invalid", "NIFTY/23100/XX", {})
    check("neither CE nor PE: 400", out["code"] == 400)
finally:
    feeds.for_user = real

print("5. THE VENDORED LIBRARY: SERVED, LICENSED, PINNED")
V = os.path.join(HERE, "vendor")
js = open(os.path.join(V, "lightweight-charts.standalone.production.js"), encoding="utf-8").read()
readme = open(os.path.join(V, "README.md"), encoding="utf-8").read()
check("TradingView Lightweight Charts 5.2.1, Apache-2.0, as its own header says", "Lightweight Charts" in js[:300]
      and "v5.2.1" in js[:300] and "Apache License 2.0" in js[:300])
check("its checksum is recorded and matches the file", hashlib.sha256(js.encode("utf-8")).hexdigest() in readme)
check("the license and the notice are kept beside it", os.path.getsize(os.path.join(V, "LICENSE-lightweight-charts")) > 10000
      and "TradingView" in open(os.path.join(V, "NOTICE-lightweight-charts")).read())
check("the API the popup uses exists in this build", all(w in js for w in ("createChart", "CandlestickSeries", "createPriceLine", "LineStyle", "autoSize")))
h, out = handler()
h._vendor("lightweight-charts.standalone.production.js")
check("served with the JavaScript content type", out["code"] == 200 and len(out["body"]) > 100000)
h = object.__new__(web_server.Handler); got = {}
h._send = lambda body, ctype="text/html", code=200: got.update(ctype=ctype, code=code)
h._vendor("lightweight-charts.standalone.production.js")
check("...with the right type", got["ctype"].startswith("application/javascript"), got)
for bad in ("../config.py", "..%2fconfig.py", "/etc/passwd", "vendor/../config.py", "README.md", "lightweight-charts.standalone.production.js/../../config.py"):
    got.clear(); h._vendor(bad)
    check(f"{bad!r} is not served - only the allowlisted file is, whoever calls it", got.get("code") == 404, got)
h._vendor("nothing-here.js")
check("a missing file: 404", got["code"] == 404)

print("6. THE PAGE")
SRC = open(os.path.join(HERE, "web_server.py")).read()
check("the library is loaded by the popup on first open, from this server - not on every page and not from another domain",
      'el.src = "/vendor/lightweight-charts.js"' in SRC and "<script src=" not in SRC.split("ONE CONTRACT")[0][-2000:]
      and 'path == "/vendor/lightweight-charts.js"' in SRC and "cdn.jsdelivr" not in SRC and "unpkg" not in SRC)
check("the content security policy still names no other script source", "script-src" not in SRC.split("Content-Security-Policy")[1][:500])
check("candles come from the venue every 60 s; the price moves them every second",
      "OC.timer = setInterval(ocFetch, 60000)" in SRC and "OC.ticker = setInterval(ocTick, 1000)" in SRC and "/api/option_tick/" in SRC)
check("the forming candle is updated from the quote, and only while it is live", "if(q.live && OC.series)" in SRC and "OC.series.update(nb)" in SRC)
check("a refresh of the venue's candles keeps the candle the ticks built", "if(f.time === last.time)" in SRC and "bars.push(f)" in SRC)
check("the open ticket's target, stop and entry are price lines", "createPriceLine(" in SRC and '"T1"' in SRC and '"SL"' in SRC and '"Entry"' in SRC)
check("...and the price axis is scaled to include them, so a stop outside the candles' range is still on screen",
      "autoscaleInfoProvider: ocScale" in SRC and "OC.levelVals = [lv.t1, lv.t2, lv.t3, lv.stop, lv.entry]" in SRC)
check("the axis reads IST and offers 1, 5 and 15 minutes", "const IST_S = 19800" in SRC and 'data-otf="1m"' in SRC and 'data-otf="15m"' in SRC)
check("the tool's own routes: the tick endpoint is routed, and kept out of the bot's tool list with its reason",
      'path.startswith("/api/option_tick/")' in SRC and '"/api/option_tick/"' in open(os.path.join(HERE, "bot_data.py")).read())
check("the TradingView tab tells the truth about delay: Bitcoin live, the Indian indices 15 minutes late",
      "delayed 15 minutes" in SRC and "Bitcoin (Bitstamp) is live" in SRC and "does not list these option" in SRC)

print("7. THE MERGE LOGIC, RUN FOR REAL IN NODE")
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available to run the page's own functions", False, "install node to run this section")
else:
    a = SRC.index("const IST_S"); b = SRC.index("// The library is 190 KB")
    prog = SRC[a:b] + r'''
const assert = require("assert");
const T = 1_790_000_000, step = 300, bucket = Math.floor(T / step) * step + IST_S;
const last = {time: bucket, open: 100, high: 105, low: 98, close: 102};
assert.deepStrictEqual(ocMerge(last, 107, T, step), {time: bucket, open: 100, high: 107, low: 98, close: 107});
assert.deepStrictEqual(ocMerge(last, 95, T, step), {time: bucket, open: 100, high: 105, low: 95, close: 95});
assert.deepStrictEqual(ocMerge(last, 110, T + step, step), {time: bucket + step, open: 110, high: 110, low: 110, close: 110});
assert.strictEqual(ocMerge(last, 90, T - step, step), null);
assert.deepStrictEqual(ocMerge(null, 50, T, step), {time: bucket, open: 50, high: 50, low: 50, close: 50});
for (const s of [60, 300, 900]) assert.strictEqual(IST_S % s, 0);
const iso = "2026-09-21T09:15:00+05:30", epoch = Math.floor(Date.parse(iso) / 1000);
const bars = ocBars([["2026-09-21T09:20:00+05:30", 2, 3, 1, 2.5, 10], [iso, 1, 2, 0.5, 1.5, 5], [iso, 9, 9, 9, 9, 1], ["bad", 1, 1, 1, 1, 1], ["2026-09-21T09:25:00+05:30", "x", 1, 1, 1, 1]]);
assert.strictEqual(bars.length, 2); assert.strictEqual(bars[0].time, epoch + IST_S); assert.strictEqual(bars[1].time, epoch + 300 + IST_S);
assert.strictEqual(ocMerge(bars[0], 1.7, epoch + 120, 300).time, bars[0].time);
'''
    c = SRC.index("function ocScale("); d = SRC.index("function ocChart(){")
    prog += SRC[c:d] + r'''
OC.levelVals = [];
const orig = () => ({priceRange: {minValue: 100, maxValue: 110}, margins: {above: 0.1, below: 0.1}});
assert.deepStrictEqual(ocScale(orig).priceRange, {minValue: 100, maxValue: 110});          // no levels: the library's own range
OC.levelVals = [82, 88, 118, 132, 150];
assert.deepStrictEqual(ocScale(orig).priceRange, {minValue: 82, maxValue: 150});           // a stop below and a target above are both in
assert.deepStrictEqual(ocScale(orig).margins, {above: 0.1, below: 0.1});
assert.strictEqual(ocScale(() => null), null);                                             // no candles yet: nothing to widen
console.log("ok");
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("bucketing, merging, ordering, the IST shift and the price-axis range behave as the popup relies on", r.returncode == 0 and r.stdout.strip() == "ok",
          (r.stderr or r.stdout)[:300])

print("STRIKE LIVE TEST PASSED" if not fails else f"STRIKE LIVE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
