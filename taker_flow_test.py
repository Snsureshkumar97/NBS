#!/usr/bin/env python3
"""Bitcoin's taker flow for the AI desk: the tape of prints, its windows and
their honesty about coverage, the socket's two message shapes, the feed that
serves it, the Gann tab that shows it, the bot that reads it and the desk that
stamps it on its decisions. Fakes only - no socket, no venue, no account."""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config
config.ENABLE_CRYPTO = True
import ai_desk
import delta_provider as dpv
import feeds
import market_bot as mb
import taker_flow as tf
import web_server

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


NOW = 1_790_010_137.0                                       # a fixed "now", so no test depends on the clock


def pr(dt_s, price, size, taker="buy", ago=True):
    """One print as Delta sends it: microsecond timestamp, price as text, size in contracts."""
    t = NOW - dt_s if ago else NOW + dt_s
    return {"timestamp": int(t * 1e6), "price": str(price), "size": size,
            "buyer_role": "taker" if taker == "buy" else "maker", "seller_role": "taker" if taker == "sell" else "maker"}


print("1. WHO WAS THE TAKER")
check("a taker buyer is +1, a taker seller -1", tf.side_of({"buyer_role": "taker", "seller_role": "maker"}) == 1
      and tf.side_of({"buyer_role": "maker", "seller_role": "taker"}) == -1)
check("a print with no clear aggressor is not counted at all",
      tf.side_of({"buyer_role": "maker", "seller_role": "maker"}) is None and tf.side_of({"buyer_role": "taker", "seller_role": "taker"}) is None
      and tf.side_of({}) is None)
t = tf.Tape()
n = t.add([pr(5, 85000, 10), {"timestamp": "junk", "price": "1", "size": 1, "buyer_role": "taker", "seller_role": "maker"},
           pr(4, "x", 3), pr(3, 85001, 0), pr(2, 85002, -5), {"price": "85000", "size": 2}], now=NOW)
check("only the well-formed print is kept; nonsense is refused and counted", n == 1 and t.dropped == 5, (n, t.dropped))
rows, cov, at = t.rows()
check("timestamps are microseconds and become seconds", abs(rows[0][0] - (NOW - 5)) < 1e-6 and rows[0][2] == 10 and rows[0][3] == 1)

print("2. THE WINDOWS: SUMS IN BITCOIN, NET, SHARE, PRICE MOVE")
t = tf.Tape()
t.add([pr(3500, 84000, 1000, "sell"), pr(890, 85000, 2000, "buy"), pr(700, 85100, 500, "buy"), pr(200, 85200, 250, "sell"),
       pr(50, 85300, 100, "buy"), pr(10, 85300, 40, "sell")], snapshot=True, now=NOW)
r = tf.reading(t, now=NOW)
w = r["windows"]
check("contracts of 0.001 BTC: the last minute holds 100 buy and 40 sell contracts = 0.100 and 0.040 BTC",
      w["1m"]["taker_buy"] == 0.1 and w["1m"]["taker_sell"] == 0.04 and w["1m"]["cvd"] == 0.06 and w["1m"]["prints"] == 2, w["1m"])
check("net and share of volume: 0.06 of 0.14 BTC is +42.9%", w["1m"]["cvd_pct_of_volume"] == 42.9)
check("the 15-minute window: buys 2000+500+100 and sells 250+40 contracts -> 2.6 and 0.29 BTC, net +2.31, +79.9% of 2.89 BTC",
      w["15m"]["taker_buy"] == 2.6 and w["15m"]["taker_sell"] == 0.29 and w["15m"]["cvd"] == 2.31
      and w["15m"]["cvd_pct_of_volume"] == 79.9, w["15m"])
check("the 60-minute window takes in the earlier sell too", w["60m"]["taker_sell"] == 1.29 and w["60m"]["cvd"] == 1.31 and w["60m"]["prints"] == 6)
check("the volume-weighted price and the move across the window come from the same prints",
      w["15m"]["first_price"] == 85000 and w["15m"]["last_price"] == 85300 and w["15m"]["price_change_pct"] == 0.353
      and 85000 < w["15m"]["vwap"] < 85300, w["15m"])
check("a window with no prints says nothing rather than zero-dividing", tf.reading(t, now=NOW + 7000)["windows"]["1m"]["cvd_pct_of_volume"] is None)

print("3. COVERAGE: A WINDOW SAYS WHEN THE TAPE DOES NOT REACH IT")
check("the tape starts at the oldest print, and the reading says how long it has held", r["tape_covers_minutes"] > 58 and r["windows"]["60m"]["complete"] is False,
      (r["tape_covers_minutes"], r["windows"]["60m"]["complete"]))
check("...so the 1, 5 and 15 minute windows are complete and the 60 minute one, at 58 minutes of tape, is not",
      [w[k]["complete"] for k in ("1m", "5m", "15m", "60m")] == [True, True, True, False])
t2 = tf.Tape(); t2.add([pr(20, 85000, 10), pr(5, 85000, 10, "sell")], snapshot=True, now=NOW)
r2 = tf.reading(t2, now=NOW)
check("twenty seconds after a restart not even the last minute is complete - every window is marked partial",
      [r2["windows"][k]["complete"] for k in ("1m", "5m", "15m", "60m")] == [False, False, False, False], r2["tape_covers_minutes"])
t2 = tf.Tape(); t2.add([pr(70, 85000, 10), pr(5, 85000, 10, "sell")], snapshot=True, now=NOW)
r2 = tf.reading(t2, now=NOW)
check("with a minute and ten seconds of tape only the last minute is complete - the 5, 15 and 60 minute figures stay partial",
      [r2["windows"][k]["complete"] for k in ("1m", "5m", "15m", "60m")] == [True, False, False, False], r2["tape_covers_minutes"])
check("and the words are withheld until the 15 minutes are really covered", r2["in_words"] is None)

print("4. THE SOCKET'S TWO MESSAGE SHAPES")
t = tf.Tape()
t.add([pr(30, 85000, 5), pr(20, 85001, 5), pr(10, 85002, 5)], snapshot=True, now=NOW)
check("a snapshot fills an empty tape", len(t.rows()[0]) == 3)
added = t.add([pr(20, 85001, 5), pr(10, 85002, 5), pr(5, 85003, 7)], snapshot=True, now=NOW)
check("a snapshot that overlaps adds only what is new - nothing counted twice", added == 1 and len(t.rows()[0]) == 4 and t.gaps == 0)
added = t.add([pr(1, 85010, 9)], now=NOW)
check("a live print is appended", added == 1 and len(t.rows()[0]) == 5)
before = len(t.rows()[0])
t.add([pr(1, 85010, 9)], now=NOW)
check("...and the same print seen again is not double counted only across a snapshot (a live repeat is a new print)", len(t.rows()[0]) == before + 1)
t = tf.Tape()
t.add([pr(300, 85000, 5), pr(290, 85001, 5)], snapshot=True, now=NOW - 280)
t.add([pr(15, 85100, 3), pr(10, 85101, 3)], snapshot=True, now=NOW)        # the socket was down: minutes of prints missed
check("a snapshot that starts after the newest print held means prints were missed - the tape starts again, "
      "and coverage starts from the snapshot, not from before the gap",
      t.gaps == 1 and len(t.rows()[0]) == 2 and abs(t.covered_from - (NOW - 15)) < 1e-6, (t.gaps, t.covered_from))
t = tf.Tape(keep_s=600)
t.add([pr(2000, 85000, 5), pr(1500, 85000, 5), pr(100, 85000, 5)], snapshot=True, now=NOW)
check("prints older than the tape's keep time are dropped, and coverage moves up with them",
      len(t.rows()[0]) == 1 and t.covered_from > NOW - 700, (len(t.rows()[0]), NOW - t.covered_from))

print("5. FIVE-MINUTE STEPS AND THE LARGEST PRINTS")
t = tf.Tape()
t.add([pr(100, 85000, 300, "buy"), pr(90, 85010, 100, "sell"), pr(1000, 84900, 600, "sell"), pr(5, 85050, 700, "buy"),
       pr(4, 85051, 20, "sell")], snapshot=True, now=NOW)
r = tf.reading(t, now=NOW)
st = r["five_minute_steps"]
check("twelve steps, oldest first, the newest still forming", len(st) == 12 and st[-1]["forming"] and not st[0]["forming"])
check("each step's net is buy minus sell in BTC: the forming step holds +0.88 of 1.12, an older one the 0.6 sold",
      st[-1]["cvd"] == 0.88 and st[-1]["volume"] == 1.12 and st[-1]["close"] == 85051.0
      and any(s["cvd"] == -0.6 and s["volume"] == 0.6 for s in st[:-1]), st[-3:])
check("the steps are labelled in IST", all(len(s["until"]) == 5 and s["until"][2] == ":" for s in st)
      and dt.datetime.fromtimestamp(NOW, tf.IST).strftime("%H:%M") in [s["until"] for s in st])
big = r["large_prints_15m"]
check("large prints are 0.5 BTC and up, biggest first, with side and price; the 15-minute limit keeps the older one out",
      [b["size"] for b in big] == [0.7] and big[0]["side"] == "buy" and big[0]["price"] == 85050, big)

print("6. THE SENTENCE - WHAT HAPPENED, NEVER WHAT WILL")
def words(prints):
    """A print just outside the 15 minutes lets the tape cover them; the ones
    given are inside, oldest first."""
    t = tf.Tape(); t.add([pr(905, 85000, 1, "buy")] + prints, snapshot=True, now=NOW)
    return tf.reading(t, now=NOW)["in_words"]
up_sell = words([pr(890, 85000, 100, "buy"), pr(600, 85100, 900, "sell"), pr(10, 85400, 300, "sell")])
check("price up and takers selling: the move and the flow disagree, said plainly",
      up_sell and "rose" in up_sell and "net sellers" in up_sell and "disagree" in up_sell, up_sell)
dn_buy = words([pr(890, 85000, 100, "sell"), pr(600, 84900, 900, "buy"), pr(10, 84600, 300, "buy")])
check("price down and takers buying is the mirror image", dn_buy and "fell" in dn_buy and "net buyers" in dn_buy and "disagree" in dn_buy, dn_buy)
agree = words([pr(890, 85000, 100, "buy"), pr(600, 85100, 900, "buy"), pr(10, 85400, 300, "buy")])
check("price and flow the same way: it agrees, and that is all it claims", agree and "agrees" in agree and "will" not in agree, agree)
bal = words([pr(890, 85000, 500, "buy"), pr(600, 85100, 480, "sell"), pr(10, 85400, 20, "buy")])
check("flow that leans under 10% is called balanced", bal and "balanced" in bal, bal)
tiny = words([pr(890, 85000, 100, "buy"), pr(600, 85002, 900, "sell"), pr(10, 85003, 300, "sell")])
check("a price move under 0.15% is not dressed up as agreement or disagreement: a flat price with a lean is reported as that",
      tiny and "disagree" not in tiny and "agrees" not in tiny and "flat" in tiny and "net sellers" in tiny, tiny)

print("7. LIVE OR NOT, AND WHAT A DECISION IS STAMPED WITH")
t = tf.Tape(); t.add([pr(10, 85000, 5)], snapshot=True, now=NOW)
check("no print for 90 seconds and the reading says it is not live", tf.reading(t, now=NOW + 60)["live"] is True
      and tf.reading(t, now=NOW + 200)["live"] is False and tf.reading(t, now=NOW + 200)["seconds_since_last_print"] == 200)
check("an empty tape has no reading at all", tf.reading(tf.Tape(), now=NOW) is None and tf.reading(None, now=NOW) is None and tf.brief(None) is None)
t = tf.Tape(); t.add([pr(905, 85000, 1, "buy"), pr(890, 85000, 100, "buy"), pr(10, 85400, 300, "buy")], snapshot=True, now=NOW)
b = tf.brief(tf.reading(t, now=NOW))
check("the stamp is five small numbers and two flags, nothing else",
      set(b) == {"cvd_5m_pct", "cvd_15m_pct", "price_15m_pct", "complete_15m", "live", "covers_min"} and b["complete_15m"] is True, b)

print("8. THE STREAMER: DELTA'S REAL MESSAGE SHAPES")
st = dpv.DeltaStreamer()
st.subscribe_trades("BTCUSD")
check("asking for trades registers the channel and a tape for that symbol", ("all_trades", "BTCUSD") in st._subs and st.tape_for("BTCUSD") is not None
      and st.tape_for("XAUTUSD") is None)
snap_msg = {"type": "all_trades_snapshot", "symbol": "BTCUSD",
            "trades": [pr(1, 85002, 4, "sell"), pr(2, 85001, 6, "buy"), pr(3, 85000, 2, "buy")]}    # newest first, as Delta sends them
live_msg = dict(pr(0.5, 85003, 8, "buy"), type="all_trades", symbol="BTCUSD", product_id=27)
before = st.last_tick_at
ret = [st._handle(snap_msg, now=NOW), st._handle(live_msg, now=NOW)]
rows = st.tape_for("BTCUSD").rows()[0]
check("a snapshot and a print both land on the tape, oldest first", [r[2] for r in rows] == [2, 6, 4, 8] and [r[3] for r in rows] == [1, 1, -1, 1], rows)
check("a print is not a price move: it does not touch the tick clock or claim to have moved a price", ret == [False, False] and st.last_tick_at == before)
st._handle(dict(live_msg, symbol="ETHUSD"), now=NOW)
check("a symbol nobody asked for is ignored", st.tape_for("ETHUSD") is None)
st._handle({"type": "all_trades_snapshot", "symbol": "BTCUSD", "trades": None}, now=NOW)
check("a malformed snapshot changes nothing and raises nothing", len(st.tape_for("BTCUSD").rows()[0]) == 4)

print("9. THE FEED: BITCOIN ONLY, ON THE CRYPTO MARKET")
check("it is switched on for Bitcoin, with the contract's size, and not for gold",
      config.INSTRUMENTS["BTC"].get("taker_flow") is True and config.INSTRUMENTS["BTC"]["flow_unit_per_contract"] == 0.001
      and not config.INSTRUMENTS["GOLD"].get("taker_flow"))


class FakeStreamer:
    HOST = "x"
    def __init__(self): self.subs = []; self.tapes = {"BTCUSD": tf.Tape()}; self.last_error = None
    def start(self): return True
    def subscribe_index(self, s): self.subs.append(("index", s))
    def subscribe_trades(self, s): self.subs.append(("trades", s))
    def tape_for(self, s): return self.tapes.get(s)


orig_ds = feeds.DeltaStreamer
feeds.DeltaStreamer = FakeStreamer
try:
    f = feeds.Feed("t:flow", "flow@example.invalid", "crypto")
    f._start_crypto_stream()
    check("starting the crypto socket asks for the Bitcoin perpetual's trades - and only that",
          ("trades", "BTCUSD") in f.dstream.subs and not any(k == "trades" and s != "BTCUSD" for k, s in f.dstream.subs), f.dstream.subs)
    check("before any print there is no reading", f.taker_flow("BTC") is None and f.flow_readings() == {})
    # The feed reads the wall clock, so these prints are stamped against it - a fixed NOW would age out of the
    # one-minute window as the day goes on (this test passed for hours and then failed on a clock, not a bug).
    live = lambda ago, price, size, taker="buy": dict(pr(0, price, size, taker), timestamp=int((time.time() - ago) * 1e6))
    f.dstream.tapes["BTCUSD"].add([live(20, 85000, 10), live(5, 85010, 4, "sell")], snapshot=True, now=time.time())
    real_time = time.time
    r = f.taker_flow("BTC")
    check("once prints arrive the feed serves the reading", r and r["symbol"] == "BTCUSD perpetual" and r["windows"]["1m"]["taker_buy"] == 0.01, r and r["windows"]["1m"])
    f.dstream.tapes["XAUTUSD"] = tf.Tape()
    f.dstream.tapes["XAUTUSD"].add([live(20, 4400, 10), live(5, 4401, 4, "sell")], snapshot=True, now=time.time())
    check("gold and anything else without the flag get none - even if a tape for it existed",
          f.taker_flow("GOLD") is None and f.taker_flow("NIFTY") is None)
    check("the snapshot's `flow` for the crypto market is the taker flow per instrument", list(f.flow_readings()) == ["BTC"])
    # A reading scans the whole tape in plain Python and was asked for afresh by every page poll, live pass and AI
    # decision - 16% of the server's CPU in a 25 Sep 2026 profile - so it is kept for two seconds, as a copy.
    n = {"c": 0}; real_reading = feeds.taker_flow.reading
    def counting(*a, **k):
        n["c"] += 1
        return real_reading(*a, **k)
    feeds.taker_flow.reading = counting
    try:
        f._taker_flow_cache.clear()
        a1 = f.taker_flow("BTC"); a2 = f.taker_flow("BTC"); a3 = f.taker_flow("BTC")
        check("three asks inside two seconds scan the tape once", n["c"] == 1 and a1 == a2 == a3, n)
        a1["windows"]["1m"]["taker_buy"] = -1
        check("a caller's edits to its reading do not reach the next caller", f.taker_flow("BTC")["windows"]["1m"]["taker_buy"] == 0.01)
        f._taker_flow_cache["BTC"] = (time.time() - 3, f._taker_flow_cache["BTC"][1])
        f.taker_flow("BTC")
        check("after two seconds it reads the tape again", n["c"] == 2, n)
        g = feeds.Feed("t:flow2", "flow2@example.invalid", "crypto")
        g._start_crypto_stream()
        check("no print yet: no reading", g.taker_flow("BTC") is None)
        g.dstream.tapes["BTCUSD"].add([live(5, 85010, 4, "sell")], snapshot=True, now=time.time())
        check("and the first print shows up at once - an empty answer is never kept", g.taker_flow("BTC") is not None)
    finally:
        feeds.taker_flow.reading = real_reading
    f.dstream = None
    check("no socket, no reading, no error", f.taker_flow("BTC") is None)
finally:
    feeds.DeltaStreamer = orig_ds

print("10. THE GANN TAB'S PAYLOAD")
class OneFeed:
    def __init__(self, flow): self._flow = flow
    def snapshot(self): return {"indices": {"BTC": {"spot": 85000.0}, "GOLD": {"spot": 4400.0}}}
    def candles(self, k): raise RuntimeError("no candles in this test")
    def taker_flow(self, k): return self._flow if k == "BTC" else None
def handler(market="crypto"):
    h = object.__new__(web_server.Handler); out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, code=code)
    h._current_market = lambda: market
    return h, out
orig_fu = feeds.for_user
try:
    sample = tf.reading(t, now=NOW)
    feeds.for_user = lambda user, market=None, start=True: OneFeed(sample)
    h, out = handler(); h._api_gann("me@example.invalid", {"index": ["BTC"]}); d = json.loads(out["body"])
    check("Bitcoin's Gann payload carries the taker flow beside its levels", d["index"] == "BTC" and d["taker_flow"]["symbol"] == "BTCUSD perpetual"
          and "nearest_support" in d)
    h, out = handler(); h._api_gann("me@example.invalid", {"index": ["GOLD"]}); d = json.loads(out["body"])
    check("gold's has none - the key is absent, not empty", "taker_flow" not in d)
    feeds.for_user = lambda user, market=None, start=True: OneFeed(None)
    h, out = handler(); h._api_gann("me@example.invalid", {"index": ["BTC"]}); d = json.loads(out["body"])
    check("Bitcoin with no prints yet: the levels still come, the flow key is absent", "taker_flow" not in d and "nearest_support" in d)
finally:
    feeds.for_user = orig_fu

print("11. THE BOT READS IT, UNDER THE RIGHT NAME")
snap = {"feed": "ok", "indices": {"BTC": {"spot": 85000.0}, "NIFTY": {"spot": 23400.0}}, "why": {}, "tickets": {},
        "session": {}, "flow": {"BTC": {"marker": "taker"}, "NIFTY": {"marker": "futures"}}}
cb = json.loads(mb.build_context(snap, "crypto", "BTC", user=None))
cn = json.loads(mb.build_context(snap, "nse_index", "NIFTY", user=None))
check("on the crypto market the flow arrives as taker_flow and there is no futures key", cb.get("taker_flow") == {"marker": "taker"}
      and "futures_and_order_flow" not in cb, list(cb)[:20])
check("on the Indian market nothing changed: futures_and_order_flow, and no taker_flow", cn.get("futures_and_order_flow") == {"marker": "futures"}
      and "taker_flow" not in cn)
for name, prompt in (("Ask TradePicker", mb.SYSTEM), ("the AI desk", mb.DESK_SYSTEM)):
    check(f"{name} is told what each number means and that it is untested",
          "taker_flow (Bitcoin only, always in the snapshot)" in prompt and "cvd_pct_of_volume" in prompt
          and "has not been tested" in prompt and "complete false covers less time" in prompt and "say in your reason" in prompt)
import bot_data
spec = next(s for s in bot_data.SOURCES if s["name"] == "get_gann")
check("the Gann lookup the bot can call says the payload carries it too", "taker_flow" in spec["description"])

print("12. THE DESK STAMPS ITS DECISIONS WITH IT")
class DeskFeed:
    def __init__(self, r=None, boom=False): self._r, self._boom = r, boom
    def taker_flow(self, k):
        if self._boom: raise RuntimeError("socket gone")
        return self._r
def desk(feed):
    d = ai_desk.AIDesk.__new__(ai_desk.AIDesk)
    d.feed, d.lock = feed, threading.RLock()
    d.recent, d.decision_no, d.day = [], {}, None
    d.decisions_path = os.path.join(tempfile.mkdtemp(), "ai_decisions.jsonl")
    d.now = lambda: dt.datetime(2026, 9, 21, 15, 0, 0)
    return d
good = tf.reading(t, now=NOW)
d = desk(DeskFeed(good))
e = d._record("BTC", "entry", "wait", "quiet")
check("an entry decision carries the small stamp", e["taker_flow"] == tf.brief(good), e.get("taker_flow"))
check("...and so does a review", "taker_flow" in d._record("BTC", "review", "hold", "ok"))
check("...but the tool's own rule exits and the day's cap do not - they are not decisions the bot made",
      "taker_flow" not in d._record("BTC", "rule", "exit", "give-back") and "taker_flow" not in d._record("BTC", "cap", "none", "cap"))
check("no reading, no key - nothing is invented", "taker_flow" not in desk(DeskFeed(None))._record("BTC", "entry", "wait", "x"))
check("a feed that has no such method (the Indian one) is untouched", "taker_flow" not in desk(object())._record("NIFTY", "entry", "wait", "x"))
check("a failure reading it never stops a decision being recorded", desk(DeskFeed(boom=True))._record("BTC", "entry", "wait", "x")["action"] == "wait")
line = json.loads(open(d.decisions_path).read().strip().split("\n")[0])
check("the stamp is in the permanent decision log too, where a later study will look", line["taker_flow"] == tf.brief(good))

print("13. THE PAGE: A CARD ON THE GANN TAB, RUN FOR REAL IN NODE")
SRC = open(os.path.join(HERE, "web_server.py")).read()
check("the card, its three tables and the call from the tab are on the page",
      'id="gannflowcard"' in SRC and 'id="gannflowwin"' in SRC and 'id="gannflowsteps"' in SRC and "gannFlowPaint(d.taker_flow);" in SRC
      and "gannFlowPaint(null);" in SRC)
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available to run the page's own function", False, "install node")
else:
    a = SRC.index("function gannFlowPaint(f){"); b = SRC.index("setInterval(() => { if(TAB === \"gann\"")
    prog = r'''
const assert = require("assert");
const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const num = (v, d) => Number(v).toFixed(d);
const EL = {};
function $(id){ return EL[id] || (EL[id] = {id, hidden: false, innerHTML: "", textContent: ""}); }
const TABLES = {};
function scrTable(id, rows, cols, empty){ TABLES[id] = {rows, cols, empty, html: rows.map(r => cols.map(([h, f]) => f(r)).join("")).join("|")}; }
''' + SRC[a:b] + r'''
const flow = {symbol: "BTCUSD perpetual", unit: "BTC", live: true, seconds_since_last_print: 3, tape_covers_minutes: 22.4,
  windows: {"1m": {complete: true, taker_buy: 0.5, taker_sell: 0.2, cvd: 0.3, cvd_pct_of_volume: 42.9, price_change_pct: 0.02},
            "5m": {complete: true, taker_buy: 1.5, taker_sell: 2.5, cvd: -1, cvd_pct_of_volume: -25, price_change_pct: -0.1},
            "60m": {complete: false, taker_buy: 0, taker_sell: 0, cvd: 0, cvd_pct_of_volume: null, price_change_pct: null}},
  five_minute_steps: [{until: "20:30", cvd: 1.1, volume: 3, close: 85010.5, forming: false}, {until: "20:33", cvd: -0.4, volume: 1, close: null, forming: true}],
  large_prints_15m: [{at: "20:31:02", side: "buy", size: 2.5, price: 85000}],
  in_words: "Over 15 minutes price rose 0.31% while takers were net sellers (-14% of volume): the move and the flow disagree.",
  note: "Not backtested."};
gannFlowPaint(flow);
assert.strictEqual(EL.gannflowcard.hidden, false, "shown when there is a reading");
assert.ok(EL.gannflowstats.innerHTML.includes("live") && EL.gannflowstats.innerHTML.includes("22 min"), "live and how much tape is held");
assert.ok(TABLES.gannflowwin.rows.length === 3 && TABLES.gannflowwin.html.includes("60m · partial") && !TABLES.gannflowwin.html.includes("1m · partial"),
          "a window the tape does not cover is labelled partial");
assert.ok(TABLES.gannflowwin.html.includes("−1.0"), "a negative net shows a real minus sign: " + TABLES.gannflowwin.html);
assert.ok(TABLES.gannflowsteps.rows[0].until === "20:33" && TABLES.gannflowsteps.html.includes("· forming"), "newest step first, the forming one marked");
assert.ok(EL.gannflownote.textContent.includes("disagree") && EL.gannflownote.textContent.includes("Largest prints, 15 min: 20:31:02 buy 2.50 @ 85000.0")
          && EL.gannflownote.textContent.includes("Not backtested."), "the sentence, the big prints and the caveat");
flow.live = false; flow.seconds_since_last_print = 400;
gannFlowPaint(flow);
assert.ok(EL.gannflowstats.innerHTML.includes("no recent prints") && EL.gannflowstats.innerHTML.includes("var(--warn)"), "a stale tape is flagged");
gannFlowPaint(undefined);
assert.strictEqual(EL.gannflowcard.hidden, true, "no flow (gold, the Indian indices): the card is hidden");
flow.in_words = "<img src=x onerror=1>"; gannFlowPaint(flow);
assert.ok(!EL.gannflowstats.innerHTML.includes("<img"), "nothing from the payload is run as markup");
console.log("ok");
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("the card shows the windows, marks partial ones, orders the steps, escapes text and hides itself when there is no flow",
          r.returncode == 0 and r.stdout.strip() == "ok", (r.stderr or r.stdout)[-500:])

print("TAKER FLOW TEST PASSED" if not fails else f"TAKER FLOW TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
