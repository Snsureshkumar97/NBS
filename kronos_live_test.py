#!/usr/bin/env python3
"""The Kronos tab (12 Oct 2026, the user: "can you add a tab for kronos i want to see on the real data how it works").
Research only. Checks, without Kronos or PyTorch:
  1. the trading server hands the worker only CLOSED candles, once per candle, with the tool's direction then
  2. the worker's clock: the next four candle starts across the close and the weekend
  3. the worker's scoring: a call is checked against the close four candles later, in the same session only
  4. the record: Kronos's hit rate, the tool's on the same calls, live and replayed calls kept apart
  5. the page: the tab, its route, Ask TradePicker can read it, and nothing in the trading imports it"""
import datetime as dt
import json
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
os.environ["TRADING_TOOL_LOGS"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import kronos_live as kl
import kronos_worker as kw

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

IST = kl.IST
HERE = os.path.dirname(os.path.abspath(__file__))

print("1. CANDLES OUT: CLOSED ONES ONLY, ONCE EACH")
idx = pd.date_range("2026-10-13 09:15", periods=8, freq="15min", tz="Asia/Kolkata")
df = pd.DataFrame({"Open": range(100, 108), "High": range(101, 109), "Low": range(99, 107), "Close": range(100, 108)},
                  index=idx).astype(float)
now = dt.datetime(2026, 10, 13, 11, 5, tzinfo=IST)            # the 11:00 candle is still forming
rows = kl.closed_candles(df, now)
check("the forming candle is left out: 09:15 to 10:45 closed by 11:05 (10:45's closed at 11:00)",
      [r[0][11:16] for r in rows] == ["09:15", "09:30", "09:45", "10:00", "10:15", "10:30", "10:45"],
      [r[0][11:16] for r in rows])
check("a candle that closes exactly now is closed; one a minute from closing is not",
      len(kl.closed_candles(df, dt.datetime(2026, 10, 13, 11, 0, tzinfo=IST))) == 7
      and len(kl.closed_candles(df, dt.datetime(2026, 10, 13, 10, 59, tzinfo=IST))) == 6)
check("rows are [time, open, high, low, close]", rows[0] == ["2026-10-13T09:15:00+05:30", 100.0, 101.0, 99.0, 100.0], rows[0])


class Feed:
    def __init__(self, df, bias):
        self.df, self.bias = df, bias
    def candles(self, name):
        return (self.df, {"bias": self.bias}) if name == "NIFTY" else (None, None)


kl._exported.clear()
wrote = kl.export(Feed(df, "BULLISH"), now)
inp = json.load(open(os.path.join(kl.base(), "in", "NIFTY.json")))
check("written for the index that has candles, not the others", wrote == ["NIFTY"], wrote)
check("...with the tool's direction then", inp["tool"] == {"dir": 1, "bias": "BULLISH"}, inp["tool"])
check("the same candle is not written twice", kl.export(Feed(df, "BEARISH"), now) == [])
check("a new closed candle is", kl.export(Feed(df, "BEARISH"), now + dt.timedelta(minutes=15)) == ["NIFTY"])
check("bearish -1, neutral 0", kl.tool_direction({"bias": "BEARISH"}) == -1 and kl.tool_direction({"bias": "NEUTRAL"}) == 0
      and kl.tool_direction(None) == 0)

print("2. THE WORKER'S CLOCK")
t = dt.datetime(2026, 10, 13, 14, 45, tzinfo=IST)
check("inside the session: the next four 15-minute starts", [s.strftime("%H:%M") for s in kw.next_slots(t)]
      == ["15:00", "15:15", "09:15", "09:30"], [s.isoformat() for s in kw.next_slots(t)])
fri = dt.datetime(2026, 10, 16, 15, 15, tzinfo=IST)                 # a Friday's last candle
check("after Friday's last candle: Monday morning", [s.strftime("%a %H:%M") for s in kw.next_slots(fri)]
      == ["Mon 09:15", "Mon 09:30", "Mon 09:45", "Mon 10:00"], [s.strftime("%a %H:%M") for s in kw.next_slots(fri)])

print("3. THE WORKER'S SCORING")
day = [[f"2026-10-13T{h}:00+05:30" if False else ts.isoformat(), 0, 0, 0, float(c)]
       for ts, c in zip(pd.date_range("2026-10-13 09:15", periods=25, freq="15min", tz="Asia/Kolkata"), range(100, 125))]
nxt = [[ts.isoformat(), 0, 0, 0, 200.0] for ts in pd.date_range("2026-10-14 09:15", periods=4, freq="15min",
                                                                  tz="Asia/Kolkata")]
candles = day + nxt
calls = [{"t": day[0][0], "close": 100.0, "f4": 105.0}, {"t": day[22][0], "close": 122.0, "f4": 120.0},
         {"t": day[24][0], "close": 124.0, "f4": 130.0}]
check("three calls changed", kw.score(calls, candles) == 3)
check("09:15's call is checked against 10:15's close (four candles on)", calls[0]["actual"] == 104.0
      and calls[0]["actual_t"].endswith("10:15:00+05:30"), calls[0])
check("a call whose hour runs past the close is marked, never scored", calls[1].get("actual") is None
      and calls[1]["skip"] and calls[2].get("skip"), calls[1:])
check("a call is not scored before its hour has closed",
      kw.score([{"t": day[22][0], "close": 1, "f4": 2}], day[:24]) == 0)

print("4. THE RECORD")
calls = [{"close": 100, "f4": 101, "actual": 102, "tool_dir": 1},      # Kronos right, tool right, agreed
         {"close": 100, "f4": 101, "actual": 99, "tool_dir": -1},      # Kronos wrong, tool right, disagreed
         {"close": 100, "f4": 99, "actual": 98, "tool_dir": 0},        # Kronos right, tool had no direction
         {"close": 100, "f4": 99, "actual": None, "tool_dir": 1},      # not checked yet
         {"close": 100, "f4": 101, "actual": 102, "replay": True}]     # a replay
sc = kl.scorecard(calls)
check("live: 3 checked of 4, Kronos right 2 of 3", sc["live"]["calls"] == 4 and sc["live"]["checked"] == 3
      and sc["live"]["kronos_right"] == 66.7, sc["live"])
check("...the tool right 2 of the 2 where it had a direction, agreed on 1 of 2", sc["live"]["tool_checked"] == 2
      and sc["live"]["tool_right"] == 100.0 and sc["live"]["agree"] == 50.0, sc["live"])
check("replays apart", sc["replay"]["checked"] == 1 and sc["replay"]["kronos_right"] == 100.0, sc["replay"])
check("an empty record says nothing, not 0%", kl.scorecard([])["live"]["kronos_right"] is None)
pub = kl.public("nse_index", now)
check("the tab's payload: four indices, the worker not running (no heartbeat)",
      [i["index"] for i in pub["indices"]] == ["NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"] and not pub["worker"]["running"])
os.makedirs(kl.base(), exist_ok=True)
json.dump({"alive_at": (now - dt.timedelta(seconds=30)).isoformat()}, open(os.path.join(kl.base(), "worker.json"), "w"))
check("a heartbeat 30 s old: running", kl.public("nse_index", now)["worker"]["running"])
check("on crypto: a note, nothing else", "Indian indices only" in kl.public("crypto", now)["note"])

print("4b. ITS OWN TRADE (the user: \"strike and target ... by its own code or decision and stop loss\", priced from the chain)")
up_path = [["a", 0, 22370, 22340, 22360], ["b", 0, 22380, 22350, 22372], ["c", 0, 22385, 22355, 22378], ["d", 0, 22390, 22360, 22380]]
chain = {"expiry": "2026-10-13", "strikes": {"22350": {"CE": 118.4, "PE": 104.1}, "22400": {"CE": 95.0, "PE": 130.0}}}
t = kw.trade_from(22363.35, up_path, 50, chain, 0.5)
check("up: a call at the strike nearest the price, target = where it says the index will be, stop = its lowest low",
      (t["side"], t["strike"], t["entry"], t["target"], t["stop"]) == ("CE", 22350, 22363.35, 22380, 22340), t)
check("...priced from the chain: premium now, target and stop by the tool's own rule (distance x 0.5 delta)",
      t["premium"] == 118.4 and t["premium_target"] == round(118.4 + (22380 - 22363.35) * 0.5, 2)
      and t["premium_stop"] == round(118.4 - (22363.35 - 22340) * 0.5, 2) and t["expiry"] == "2026-10-13", t)
dn_path = [["a", 0, 22400, 22340, 22350], ["b", 0, 22390, 22330, 22340], ["c", 0, 22370, 22320, 22330], ["d", 0, 22360, 22310, 22320]]
t = kw.trade_from(22363.35, dn_path, 50, chain, 0.5)
check("down: a put, stop = its highest high", (t["side"], t["target"], t["stop"], t["premium"]) == ("PE", 22320, 22400, 104.1), t)
never = [["a", 0, 22390, 22370, 22380]] * 4
t = kw.trade_from(22363.35, never, 50, None)
check("a forecast that never goes against it: the stop the target's distance the other way; no chain, no premium",
      t["stop"] == round(22363.35 - (22380 - 22363.35), 2) and "premium" not in t, t)
inp_ = {"strike_step": 50, "lot_size": 65, "delta": 0.5, "chain": chain}
c_live = kw.to_call([["x", 0, 0, 0, 22363.35]], 0, up_path, 1, False, now, inp_)
c_rep = kw.to_call([["x", 0, 0, 0, 22363.35]], 0, up_path, 1, True, now, inp_)
check("the newest call is priced from the chain; a REPLAYED one is not (the chain is today's price, not then)",
      c_live["trade"]["premium"] == 118.4 and "premium" not in c_rep["trade"] and c_rep["tool_dir"] is None
      and c_live["lot_size"] == 65, (c_live["trade"], c_rep["trade"]))
check("a flat forecast is no trade", kw.trade_from(100.0, [["a", 0, 101, 99, 100.0]] * 4, 50) is None)
tr = {"side": "CE", "entry": 100.0, "target": 110.0, "stop": 95.0}
check("target first: +10", kw.trade_outcome(tr, [["a", 0, 104, 98, 103], ["b", 0, 111, 102, 109]]) == ("target", 10.0))
check("a candle touching both: the stop (the pessimistic reading)",
      kw.trade_outcome(tr, [["a", 0, 112, 94, 100]]) == ("stop", -5.0))
check("neither in the hour: out at the last close", kw.trade_outcome(tr, [["a", 0, 104, 98, 103], ["b", 0, 105, 99, 102]])
      == ("time", 2.0))
check("a put's points are the fall", kw.trade_outcome({"side": "PE", "entry": 100.0, "target": 90.0, "stop": 105.0},
                                                       [["a", 0, 101, 89, 92]]) == ("target", 10.0))
# candles with real highs and lows: close 100, 101, ... each +-0.4
real = [[ts.isoformat(), 0, float(c) + 0.4, float(c) - 0.4, float(c)]
        for ts, c in zip(pd.date_range("2026-10-13 09:15", periods=25, freq="15min", tz="Asia/Kolkata"), range(100, 125))]
calls = [{"t": real[0][0], "close": 100.0, "f4": 105.0, "lot_size": 65,
          "trade": {"side": "CE", "entry": 100.0, "target": 103.5, "stop": 99.5, "premium": 50.0, "delta": 0.5}}]
kw.score(calls, real)
check("scored with its trade: target hit +3.5 points, Rs 3.5 x 0.5 x 65 a lot ESTIMATED",
      calls[0]["result"] == "target" and calls[0]["points"] == 3.5 and calls[0]["rupees"] == round(3.5 * 0.5 * 65, 2), calls[0])
sc = kl.scorecard(calls + [{"close": 1, "f4": 2, "actual": 0.5, "result": "stop", "points": -1.5}])
check("the record counts its trades: target/stop/hour, won %, points, rupees where priced",
      (sc["live"]["trades"], sc["live"]["targets"], sc["live"]["stops"], sc["live"]["timed"], sc["live"]["won"],
       sc["live"]["points"], sc["live"]["rupees"], sc["live"]["rupees_n"]) == (2, 1, 1, 0, 50.0, 2.0, 114, 1), sc["live"])


class ChainFeed(Feed):
    def live_chain(self, name):
        return {"expiry": "2026-10-13", "strikes": [{"strike": s, "call_ltp": 1.0 * s / 100, "put_ltp": 2.0}
                                                   for s in list(range(0, 300, 50)) + list(range(22000, 22800, 50))]}
    def chain_live(self, name):
        return {(22350.0, "CE"): {"ltp": 119.9}}


cp = kl.chain_prices(ChainFeed(df, "BULLISH"), "NIFTY", 22363.35, 50)
check("the chain: the strikes within two steps of the price, the streamed price over the snapshot's",
      sorted(cp["strikes"]) == ["22250", "22300", "22350", "22400", "22450"] and cp["strikes"]["22350"]["CE"] == 119.9
      and cp["strikes"]["22400"]["CE"] == 224.0 and cp["expiry"] == "2026-10-13", cp)
check("no chain: None, and the export still goes out", kl.chain_prices(Feed(df, "x"), "NIFTY", 22363.35, 50) is None)
kl._exported.clear()
kl.export(ChainFeed(df, "BULLISH"), now)
inp = json.load(open(os.path.join(kl.base(), "in", "NIFTY.json")))
check("the export carries the strike step, lot size, the tool's delta and the chain",
      inp["strike_step"] == 50 and inp["lot_size"] == 65 and inp["delta"] == 0.5 and inp["chain"]["strikes"], inp.get("chain"))

print("5. THE PAGE, AND THE TRADING NEVER LOADS IT")
SRC = open(os.path.join(HERE, "web_server.py")).read()
check("a Kronos tab under Research, and its pane", 'data-tab="kronos"' in SRC.split('data-grp="research"')[1].split("</div>")[0]
      and '<section class="pane" data-pane="kronos">' in SRC)
check("its route and handler", 'if path == "/api/kronos":' in SRC and "def _api_kronos(self, user):" in SRC)
check("says research only on the tab itself", "<b>Research only.</b> Kronos never decides a trade here." in SRC)
import bot_data
check("Ask TradePicker can read it", any(s["name"] == "get_kronos" and "kronos" in s["tabs"] for s in bot_data.SOURCES))
for f in ("feeds.py", "tickets.py", "signal_engine.py", "live_orders.py", "exness_orders.py"):
    check(f"{f} never imports Kronos or its worker", "kronos" not in open(os.path.join(HERE, f)).read().lower())
check("the worker imports PyTorch only inside load()/forecast()", "import torch" not in
      open(os.path.join(HERE, "kronos_worker.py")).read().split("def load(")[0])

print()
print("KRONOS LIVE TEST PASSED" if not fails else f"KRONOS LIVE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
