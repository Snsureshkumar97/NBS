#!/usr/bin/env python3
"""The Gann Square of Nine calculator and the volume oscillator: the maths,
the report, the route, the tab, and the bot's tool - plus the TradingView tab
being a frame of TradingView's own page and nothing more. Fakes only."""
import json
import math
import os
import sys
import tempfile

import pandas as pd

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()           # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gann

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. THE SQUARE OF NINE")
b, a = gann.grid(25000.0)
check("the 45-degree grid around 25,000: 158^2 below, 158.25^2 above", b == 24964.0 and a == 25043.06, (b, a))
check("a full turn adds 2 to the square root", gann.level(25000.0, 360) == round((math.sqrt(25000.0) + 2) ** 2, 2))
check("180 degrees adds 1, 90 adds 0.5, 45 adds 0.25",
      gann.level(10000.0, 180) == 10201.0 and gann.level(10000.0, 90) == 10100.25 and gann.level(10000.0, 45) == 10050.06)
check("down the square as well", gann.level(10000.0, -180) == 9801.0)
b2, a2 = gann.grid(24964.0)
check("a price sitting exactly on a rung: the rung is not its own support", b2 < 24964.0 < a2, (b2, a2))
lad = gann.ladder(25000.0)
check("the ladder: four rungs each side, every rung on the 45-degree grid (prices kept to 2 decimals)",
      len(lad) == 8 and all(abs(math.sqrt(x["price"]) / 0.25 - round(math.sqrt(x["price"]) / 0.25)) < 1e-3 for x in lad))
check("each rung says which side of the spot it is on and how far", sum(1 for x in lad if x["side"] == "above") == 4
      and all(abs(x["pct_from_spot"] - (x["price"] - 25000.0) / 250.0) < 0.01 for x in lad))
check("cardinals (multiples of 90 degrees) are flagged", any(x["cardinal"] for x in lad)
      and all((x["angle"] % 90 == 0) == x["cardinal"] for x in lad))

print("2. THE VOLUME OSCILLATOR")
vo = gann.volume_oscillator([100] * 30 + [300] * 5)
check("volume jumping above its average reads positive, flat volume reads zero",
      vo.iloc[-1] > 20 and abs(vo.iloc[29]) < 1e-9, (vo.iloc[-1], vo.iloc[29]))
vo2 = gann.volume_oscillator([0] * 10)
check("no volume at all: NaN, not a division error", vo2.isna().all())

print("3. THE REPORT")
idx = pd.date_range("2026-09-18 09:15", periods=40, freq="15min", tz="Asia/Kolkata")
df = pd.DataFrame({"Open": 25000.0, "High": 25040.0, "Low": 24960.0, "Close": 25000.0,
                   "Volume": [1000.0] * 35 + [2500.0] * 5}, index=idx)
r = gann.report("NIFTY", 25010.0, df)
check("spot, nearest support and resistance, the rungs, the ladder and the study note",
      r["spot"] == 25010.0 and r["nearest_support"] == 24964.0 and r["nearest_resistance"] == 25043.06
      and [x["angle"] for x in r["rungs"]] == [45, 90, 180, 360] and len(r["ladder"]) == 8 and "not part of the signal" in r["study"])
check("distances in ATR(14) when candles are given", r.get("atr14") == 80.0 and r["resistance_in_atr"] == round(33.06 / 80, 2))
check("the volume oscillator, rising, on the future's volume", r["volume_oscillator"]["rising"] is True
      and r["volume_oscillator"]["value_pct"] > 0 and "future" in r["volume_oscillator"]["volume_of"])
r0 = gann.report("SENSEX", 82000.0, df.assign(Volume=0.0))
check("no volume on the candles: said, not invented", r0["volume_oscillator"]["value_pct"] is None
      and "near-month future" in r0["volume_oscillator"]["note"])
rb = gann.report("BTC", 80000.0, df)
check("Bitcoin's volume is the perpetual's", rb["volume_oscillator"]["volume_of"] == "the perpetual")
check("no price yet: says so", gann.report("NIFTY", None)["spot"] is None)

print("4. THE ROUTE, THE TAB AND THE BOT'S TOOL")
import bot_data
import feeds
import web_server
SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()
NS = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "nbs_site.py")).read()
check("a Gann tab in the Analysis group, its pane, and the fetch on opening it",
      'data-tab="gann"' in SRC and 'data-pane="gann"' in SRC and 'if(name === "gann") gannFetch();' in SRC
      and '"gann", "tradingview"' in SRC and 'gann:"Gann levels"' in SRC)
check("a TradingView tab whose pane is a frame of TradingView's own page - no script of theirs",
      'data-tab="tradingview"' in SRC and 'data-pane="tradingview"' in SRC and "s.tradingview.com/widgetembed" in SRC
      and "tradingview.com/tv.js" not in SRC and "embed-widget" not in SRC)
check("the content security policy allows only that frame", "frame-src https://*.tradingview.com" in SRC
      and "script-src" not in SRC.split("Content-Security-Policy")[1][:400])
check("the security page says so", "TradingView" in NS and "only when you open that tab" in NS)
src = {s["name"]: s for s in bot_data.SOURCES}
check("Ask TradePicker has get_gann for the Gann tab and its route", "get_gann" in src and src["get_gann"]["tabs"] == ("gann",)
      and src["get_gann"]["routes"] == ("/api/gann",) and "get_gann" in bot_data.LABELS)
check("the TradingView tab is excluded from the bot: nothing of the tool's to read", "tradingview" in bot_data.EXCLUDED_TABS)
import market_bot
MB = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_bot.py")).read()
check("the AI desk is handed the same tools as Ask TradePicker, so it sees the Gann levels and everything else the tool pulls",
      any(t["name"] == "get_gann" for t in bot_data.tool_specs())
      and MB.count("bot_data.tool_specs()") >= 1 and "def decide(" in MB
      and "tool_specs()" in MB.split("def decide(", 1)[1][:3000])


class FakeFeed:
    def __init__(self):
        self.spots = {"NIFTY": 25010.0}
    def snapshot(self):
        return {"indices": {"NIFTY": {"spot": 25010.0}}}
    def candles(self, name):
        return (df, None) if name == "NIFTY" else (None, None)


def handler(market="nse_index"):
    h = object.__new__(web_server.Handler)
    out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, code=code)
    h._current_market = lambda: market
    h._current_user = lambda: "me@example.invalid"
    return h, out


orig = feeds.for_user
feeds.for_user = lambda user, market=None, start=True: FakeFeed()
try:
    h, out = handler()
    h._api_gann("me@example.invalid", {"index": ["NIFTY"]})
    d = json.loads(out["body"])
    check("/api/gann: the report for the index asked for, off the live spot and the feed's candles",
          out["code"] == 200 and d["index"] == "NIFTY" and d["spot"] == 25010.0 and d["nearest_resistance"] == 25043.06
          and d["volume_oscillator"]["rising"] is True, d.get("index"))
    h, out = handler()
    h._api_gann("me@example.invalid", {})
    check("no index named: the market's first", json.loads(out["body"])["index"] == "NIFTY")
    h, out = handler()
    h._api_gann("me@example.invalid", {"index": ["BTC"]})
    check("an index outside this market is refused", out["code"] == 400)
    h, out = handler()
    h._api_gann("me@example.invalid", {"index": ["SENSEX"]})
    d = json.loads(out["body"])
    check("an index with no price yet says so rather than inventing levels", out["code"] == 200 and d["spot"] is None)
finally:
    feeds.for_user = orig

print("5. WHAT THE AI DESK AND ASK TRADEPICKER ARE HANDED WITH EVERY DECISION")
import market_bot as mb
snap = {"indices": {"NIFTY": {"spot": 25010.0}}, "tickets": {}, "session": {}, "why": {}}
seen = []
def fake_for_user(email, market=None, start=True):
    seen.append(start)
    return FakeFeed()
orig = feeds.for_user
feeds.for_user = fake_for_user
try:
    ctx = json.loads(mb.build_context(snap, "nse_index", "NIFTY", user="me@example.invalid"))
    g = ctx.get("gann_and_volume") or {}
    check("the snapshot carries the Gann levels, their distance in ATR and the volume oscillator - no lookup needed",
          g.get("nearest_support") == 24964.0 and g.get("nearest_resistance") == 25043.06 and g.get("atr14") == 80.0
          and g.get("resistance_in_atr") == round(33.06 / 80, 2) and g.get("volume_rising") is True
          and g.get("volume_oscillator_pct", 0) > 0 and "future" in g.get("volume_of", ""), g)
    check("reading them never starts a feed (start=False)", seen == [False], seen)
    check("and it stays small enough to send every time", len(json.dumps(g)) < 500, len(json.dumps(g)))
    check("no signed-in user: nothing, and no feed touched",
          json.loads(mb.build_context(snap, "nse_index", "NIFTY", user=None))["gann_and_volume"] is None)
    check("no spot yet: nothing",
          json.loads(mb.build_context({"indices": {"NIFTY": {}}}, "nse_index", "NIFTY", user="me@example.invalid"))["gann_and_volume"] is None)
    feeds.for_user = lambda email, market=None, start=True: None
    check("no feed running: nothing, and the decision goes ahead",
          json.loads(mb.build_context(snap, "nse_index", "NIFTY", user="me@example.invalid"))["gann_and_volume"] is None)
    class Boom(FakeFeed):
        def candles(self, name):
            raise RuntimeError("candles unavailable")
    feeds.for_user = lambda email, market=None, start=True: Boom()
    check("a failure while computing them never breaks a decision",
          json.loads(mb.build_context(snap, "nse_index", "NIFTY", user="me@example.invalid"))["gann_and_volume"] is None)
    feeds.for_user = lambda email, market=None, start=True: FakeFeed()
    check("an index with no candles still gets the levels from the price (volume says why it is missing)",
          json.loads(mb.build_context({"indices": {"SENSEX": {"spot": 82000.0}}, "tickets": {}, "session": {}, "why": {}},
                                       "nse_index", "SENSEX", user="me@example.invalid"))["gann_and_volume"].get("nearest_support") is not None)
finally:
    feeds.for_user = orig
check("both instruction texts explain it, as reference levels the tool's own study found no edge in",
      all("gann_and_volume" in t and "did not improve its rules on their own" in t for t in (mb.SYSTEM, mb.DESK_SYSTEM)))
check("Ask TradePicker's list of sections names the Gann levels", "Gann levels with the volume oscillator" in mb.SYSTEM)
check("the desk must look at what could stand in the way BEFORE an entry - chain, levels, Gann, order flow, news - and say so; a wait needs no checklist",
      "Before you submit an ENTRY" in mb.DESK_SYSTEM and "Gann levels for room to your target" in mb.DESK_SYSTEM
      and "a wait needs no checklist" in mb.DESK_SYSTEM)
check("the desk is offered every tool Ask TradePicker has, get_gann included, with room for the lookups the checklist needs",
      {t["name"] for t in bot_data.tool_specs()} >= {"get_gann", "get_option_chain", "get_analytics", "get_news", "get_signal"}
      and mb.MAX_TOOL_CALLS >= 8)

print("GANN TEST PASSED" if not fails else f"GANN TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
