#!/usr/bin/env python3
"""The Kronos tab (12 Oct 2026, the user: "can you add a tab for kronos i want to see on the real data how it works").
Research only. Checks, without Kronos or PyTorch:
  1. the trading server hands the worker only CLOSED candles, once per candle, with the tool's direction then
  2. the worker's clock: the next four candle starts across the close and the weekend
  3. the worker's scoring: a call is checked against the close four candles later, in the same session only
  4. the record: Kronos's hit rate, the tool's on the same calls, live and replayed calls kept apart
  5. the page: the tab, its route, Ask TradePicker can read it, and nothing in the trading imports it
Also (4f/4g) its whole-day forecast from the 09:15 candle to the close, and its trades today with the option's price
NOW for the tab and the Positions page."""
import datetime as dt
import json
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
os.environ["TRADING_TOOL_LOGS"] = tempfile.mkdtemp()
os.environ["ENABLE_CRYPTO"] = "1"             # Bitcoin and gold on Exness, as the server runs
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
check("a market Kronos does not cover: a note, nothing else", "does not run" in kl.public("spot_fx", now)["note"])

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
calls = [{"t": real[0][0], "close": 100.0, "f4": 105.0, "lot_size": 65, "taken": True,
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

print("4d. WHICH CALLS IT TRADES (the user: \"one trade at a time and skip small calls\")")
def mk(i, entry, target, stop, replay=False):
    return {"t": real[i][0], "close": entry, "f4": target, "replay": replay,
            "trade": {"side": "CE" if target > entry else "PE", "entry": entry, "target": target, "stop": stop}}
cs = [mk(0, 100.0, 101.0, 99.5),        # taken; its target is hit on the next candle (high 101.4)
      mk(1, 101.0, 102.0, 100.5),       # the first ended at candle 1 -> taken
      mk(2, 102.0, 102.05, 101.9),      # 0.05% move -> a small move
      mk(3, 103.0, 104.0, 101.0),       # reward 1 < risk 2 -> reward smaller than risk
      mk(4, 104.0, 110.0, 103.5),       # the second ended at candle 2 -> taken
      mk(5, 105.0, 107.0, 104.0),       # the one from candle 4 is still open (110 not reached, 103.5 not hit) -> skipped
      mk(20, 120.0, 121.0, 119.5),      # 14:15, the candle-4 trade long over -> taken
      mk(21, 121.0, 122.0, 120.5),      # 14:30 -> too near the close
      mk(6, 106.0, 108.0, 105.5, replay=True)]   # a replay: its own run, not blocked by the live one open then
check("decided: every call", kw.decide(cs, real) == len(cs))
got = [(c["taken"], c.get("no_trade")) for c in cs]
check("taken / skipped, each for its reason",
      got == [(True, None), (True, None), (False, "a small move"), (False, "reward smaller than risk"), (True, None),
              (False, "a trade still open"), (True, None), (False, "too near the close"), (True, None)], got)
check("a decision stays made", kw.decide(cs, real) == 0)
hour = [mk(8, 108.0, 130.0, 100.0), mk(11, 111.0, 113.0, 110.5), mk(12, 112.0, 114.0, 111.5)]
kw.decide(hour, real)
check("a trade that hits neither target nor stop holds its slot for exactly its hour: a call 3 candles on is "
      "skipped, one 4 candles on (the hour just ended) is taken",
      [(c["taken"], c.get("no_trade")) for c in hour] == [(True, None), (False, "a trade still open"), (True, None)],
      [(c["taken"], c.get("no_trade")) for c in hour])
kw.score(cs, real)
check("only TAKEN trades are played out and counted", all(("result" in c) == c["taken"] for c in cs[:7]),
      [(c["taken"], c.get("result")) for c in cs])
sc = kl.scorecard(cs)
check("the record: trades = taken ones, skipped counted by reason", sc["live"]["trades"] == 4
      and sc["live"]["skipped"] == {"a small move": 1, "a trade still open": 1, "reward smaller than risk": 1,
                                    "too near the close": 1}, (sc["live"]["trades"], sc["live"]["skipped"]))
check("the rules are what the tab says", kw.RULES == {"min_move_pct": 0.15, "min_rr": 1.0, "last_trade": "14:15",
                                                       "one_at_a_time": True}, kw.RULES)

print("4c. THE CALLS LISTED FOR AN INDEX: NEWEST LIVE FIRST, THEN THE REPLAYS")
os.makedirs(os.path.join(kl.base(), "calls"), exist_ok=True)
json.dump([{"t": "r1", "close": 1, "f4": 2, "replay": True}, {"t": "r2", "close": 1, "f4": 2, "replay": True},
           {"t": "l1", "close": 1, "f4": 2}, {"t": "l2", "close": 1, "f4": 0, "actual": 0.5}],
          open(os.path.join(kl.base(), "calls", "SENSEX.json"), "w"))
sx = next(i for i in kl.public("nse_index", now)["indices"] if i["index"] == "SENSEX")
check("live calls newest first, then the replays (newest first)", [c["t"] for c in sx["calls"]] == ["l2", "l1", "r2", "r1"],
      [c["t"] for c in sx["calls"]])
check("each with what the tab draws", set(sx["calls"][0]) >= {"t", "close", "f4", "actual", "tool_dir", "replay", "result",
                                                               "points", "rupees", "trade", "skip"}, sorted(sx["calls"][0]))

print("4e. CRYPTO: BITCOIN AND GOLD ON EXNESS (the user: \"can you add it for crypto as well\")")
import config
btc_idx = pd.date_range("2026-10-12 22:00", periods=10, freq="15min", tz="Asia/Kolkata")
bdf = pd.DataFrame({"Open": range(82000, 82010), "High": range(82050, 82060), "Low": range(81950, 81960),
                    "Close": range(82000, 82010)}, index=btc_idx).astype(float)


class CryptoFeed:
    def candles(self, name):
        return (bdf, {"bias": "BEARISH"}) if name == "BTC" else (None, None)
    def live_chain(self, name):
        raise AssertionError("an Exness CFD has no option chain - it must not be asked for one")


kl._exported.clear()
check("exported for Bitcoin", kl.export(CryptoFeed(), dt.datetime(2026, 10, 13, 0, 40, tzinfo=IST), market="crypto") == ["BTC"])
bi = json.load(open(os.path.join(kl.base(), "in", "BTC.json")))
check("...as a CFD: the crypto market, no chain, no strike step, its own lot (1 BTC), the tool's direction then",
      (bi["market"], bi["cfd"], bi["chain"], bi["strike_step"], bi["lot_size"], bi["tool"]["dir"])
      == ("crypto", True, None, None, 1.0, -1), {k: bi[k] for k in ("market", "cfd", "chain", "strike_step", "lot_size")})
t = dt.datetime(2026, 10, 12, 23, 30, tzinfo=IST)
check("its clock runs straight through midnight", [x.strftime("%d %H:%M") for x in kw.next_slots(t, continuous=True)]
      == ["12 23:45", "13 00:00", "13 00:15", "13 00:30"])
ctr = {"side": "PE", "entry": 82000.0, "target": 81800.0, "stop": 82100.0}
cc = kw.to_call([["2026-10-12T23:00:00+05:30", 0, 0, 0, 82000.0]], 0,
                [["a", 0, 82010, 81900, 81950], ["b", 0, 82000, 81850, 81900], ["c", 0, 81950, 81800, 81850],
                 ["d", 0, 81900, 81750, 81800]], -1, False, now, {"cfd": True, "lot_size": 1.0, "delta": 0.5})
check("its trade is the instrument itself: a sell, no strike, no premium", cc["trade"]["cfd"] is True
      and cc["trade"]["side"] == "PE" and cc["trade"]["strike"] is None and "premium" not in cc["trade"], cc["trade"])
night = [[ts.isoformat(), 0, float(82000 - 20 * k) + 30, float(82000 - 20 * k) - 30, float(82000 - 20 * k)]
         for k, ts in enumerate(pd.date_range("2026-10-12 23:00", periods=6, freq="15min", tz="Asia/Kolkata"))]
cc2 = dict(cc, t=night[0][0], close=82000.0, f4=81900.0,
           trade={"side": "PE", "entry": 82000.0, "target": 81800.0, "stop": 82100.0, "cfd": True})   # 0.24%, R:R 2
cs_c = [cc2]
check("no 14:15 cut-off on crypto: a 23:00 call is traded", kw.decide(cs_c, night, "crypto") == 1 and cs_c[0]["taken"],
      cs_c[0].get("no_trade"))
kw.score(cs_c, night)
check("an hour across midnight is scored; out at the hour's close (+80); the result in DOLLARS a lot (x 1 BTC)",
      cs_c[0]["actual"] == 81920.0 and cs_c[0]["result"] == "time" and cs_c[0]["usd"] == 80.0
      and "rupees" not in cs_c[0], {k: cs_c[0].get(k) for k in ("actual", "result", "points", "usd")})
gap = [[ts.isoformat(), 0, 4101.0, 4099.0, 4100.0] for ts in
       list(pd.date_range("2026-10-13 01:30", periods=4, freq="15min", tz="Asia/Kolkata"))
       + list(pd.date_range("2026-10-13 03:30", periods=4, freq="15min", tz="Asia/Kolkata"))]
gc = [{"t": gap[1][0], "close": 4100.0, "f4": 4110.0}]
kw.score(gc, gap)
check("an hour across gold's daily break is marked, not scored", gc[0].get("actual") is None and gc[0].get("skip"), gc[0])
check("the crypto rules have no cut-off time", kw.rules_for("crypto")["last_trade"] is None
      and kw.rules_for("nse_index")["last_trade"] == "14:15")
sc = kl.scorecard(cs_c)
check("the record counts the dollars", sc["live"]["rupees"] == 80 and sc["live"]["trades"] == 1, sc["live"])
pc = kl.public("crypto", now)
check("the tab on crypto: Bitcoin and gold, marked as CFDs", [i["index"] for i in pc["indices"]] == ["BTC", "GOLD"]
      and pc["cfd"] is True, (pc.get("cfd"), [i["index"] for i in pc["indices"]]))

print("4f. THE WHOLE DAY (the user: \"forecast till where it can go end of the market ... which strike and target\")")
def session(day, start_px, step_px, n=25, wig=5.0):
    """One session's 15-minute candles from 09:15, the close moving step_px a candle, each +-wig."""
    idx = pd.date_range(f"{day} 09:15", periods=n, freq="15min", tz="Asia/Kolkata")
    return [[ts.isoformat(), start_px + step_px * k, start_px + step_px * k + wig, start_px + step_px * k - wig,
             float(start_px + step_px * k)] for k, ts in enumerate(idx)]


check("24 candles follow the 09:15 one, to the 15:15 one; none after 15:15",
      kw.day_len("2026-10-13T09:15:00+05:30") == 24 and kw.day_len("2026-10-13T15:15:00+05:30") == 0)
d1 = session("2026-10-12", 25000, 10)              # yesterday: rises 10 a candle, 25000 -> 25240
d2 = session("2026-10-13", 25300, -10, n=8)        # today, to the 11:00 candle so far
cand = d1 + d2
check("yesterday's session is over; today's, at 11:00, is not", kw.session_done(cand, 0) and not kw.session_done(cand, 25))
check("the rest of a session is that day's candles only", len(kw.session_rest(cand, 0)) == 24
      and len(kw.session_rest(cand, 25)) == 7)
dc = [{"t": d1[0][0], "close": 25000.0, "f4": 25150.0, "taken": True, "lot_size": 65,
       "trade": {"side": "CE", "strike": 25000, "entry": 25000.0, "target": 25150.0, "stop": 24980.0, "premium": 120.0,
                 "delta": 0.5}},
      {"t": d2[0][0], "close": 25300.0, "f4": 25400.0, "taken": True,
       "trade": {"side": "CE", "entry": 25300.0, "target": 25400.0, "stop": 25250.0}}]
kw.score_days(dc, cand)
check("a finished day is scored: closed at 25240, its target hit (+150), rupees estimated with delta x lot",
      dc[0]["actual"] == 25240.0 and dc[0]["result"] == "target" and dc[0]["points"] == 150.0
      and dc[0]["rupees"] == 150 * 0.5 * 65, {k: dc[0].get(k) for k in ("actual", "result", "points", "rupees")})
check("today's is not scored while its session is on", dc[1].get("actual") is None and "result" not in dc[1])
dc2 = [{"t": d1[0][0], "close": 25000.0, "f4": 25500.0, "taken": True,
        "trade": {"side": "CE", "entry": 25000.0, "target": 25500.0, "stop": 24900.0}}]
kw.score_days(dc2, cand)
check("a target never reached: out at the day's last close (+240)", dc2[0]["result"] == "time" and dc2[0]["points"] == 240.0,
      dc2[0])
dc3 = [{"t": d1[0][0], "close": 25000.0, "f4": 24800.0, "taken": True,
        "trade": {"side": "PE", "entry": 25000.0, "target": 24800.0, "stop": 25100.0}}]
kw.score_days(dc3, cand)
check("a put against a rising day is stopped (-100)", dc3[0]["result"] == "stop" and dc3[0]["points"] == -100.0, dc3[0])
short = session("2026-10-09", 25000, 5, n=12) + d1
dc4 = [{"t": short[0][0], "close": 25000.0, "f4": 25100.0, "taken": False, "trade": None}]
kw.score_days(dc4, short)
check("a short session (the next one has begun) is scored on its own last candle", dc4[0]["actual"] == short[11][4], dc4[0])
for tg, stp, want in ((25100.0, 24950.0, True), (25020.0, 24990.0, "a small move"),
                      (25100.0, 24850.0, "reward smaller than risk")):
    c = {"trade": {"side": "CE", "entry": 25000.0, "target": tg, "stop": stp}}
    kw.decide_day(c)
    check(f"its rules: target {tg:.0f}, stop {stp:.0f} -> {want if want is not True else 'traded'}",
          c["taken"] is True if want is True else (c["taken"] is False and c.get("no_trade") == want), c)
c = {"trade": None}
kw.decide_day(c)
check("a flat whole-day forecast is not traded", c["taken"] is False and c["no_trade"] == "a flat forecast")
dt_ = kw.day_trade({"side": "CE", "entry": 25000.0, "target": 25080.0, "stop": 24990.0, "premium": 130.0, "delta": 0.5},
                   25120.0, 24990.0)
check("the day trade (the user: \"take the profit before the market hits the top\"): a ladder to the best point it "
      "forecasts for the day - T1 halfway (25060), T2 three-quarters (25090), target 25120; its forecast close kept; "
      "premiums by delta; no day range known -> no stop yet", (dt_["exit"], dt_["target"], dt_["stop"], dt_["close_fc"],
      dt_["t1"], dt_["t2"], dt_["premium_t1"], dt_["premium_t2"], dt_["premium_target"])
      == ("ladder", 25120.0, None, 25080.0, 25060.0, 25090.0, 160.0, 175.0, 190.0) and "premium_stop" not in dt_, dt_)
ds_ = kw.day_trade({"side": "CE", "entry": 25000.0, "target": 25080.0, "stop": 24990.0, "premium": 130.0, "delta": 0.5},
                   25120.0, 24990.0, 200.0)
check("...with a day range of 200: its STOP half of it below the entry (24900), the option's stop price 130 - 50 = 80 "
      "(the user: \"its not giving a stop loss\")", ds_["stop"] == 24900.0 and ds_["premium_stop"] == 80.0, ds_)
dsp = kw.day_trade({"side": "PE", "entry": 25000.0, "target": 24950.0, "stop": 25010.0, "premium": 40.0, "delta": 0.5},
                   25010.0, 24930.0, 200.0)
check("...a put's stop ABOVE the entry; its stop price never below 0.05", dsp["stop"] == 25100.0
      and dsp["premium_stop"] == 0.05, dsp)
lad = [["a", 0, 25040, 24980, 25030], ["b", 0, 25070, 25020, 25060], ["c", 0, 25095, 25050, 25090],
       ["d", 0, 25080, 25055, 25060]]
check("the ladder, traced: T1 touched (b) -> the stop to the entry; T2 touched (c) -> the stop to T1 (25060); the "
      "pull-back (d) closes it there, +60 kept", kw.ladder_walk(ds_, lad) == ("stop", 25060.0, 3, 25060.0),
      kw.ladder_walk(ds_, lad))
check("...still open after b: the stop now at the entry", kw.ladder_walk(ds_, lad[:2]) == (None, None, None, 25000.0))
check("...its stop first: a candle down through 24900 before anything else", kw.ladder_walk(
      ds_, [["a", 0, 25010, 24890, 24950]]) == ("stop", 24900.0, 0, 24900.0))
check("...the target touched: out there", kw.ladder_walk(ds_, [["a", 0, 25130, 24990, 25100]])[:2] == ("target", 25120.0))
rng_c = [[ts.isoformat(), 0, 100.0 + 10 * k, 100.0, 105.0] for k, ts in
         enumerate(pd.date_range("2026-09-28 09:15", periods=12, freq="1D", tz="Asia/Kolkata"))]
check("the day range: the average (high - low) of the 10 sessions before the trade's - fewer than 10, none",
      kw.day_range(rng_c, "2026-10-09T09:15:00+05:30") == sum(10 * k for k in range(1, 11)) / 10
      and kw.day_range(rng_c, "2026-10-05T09:15:00+05:30") is None, kw.day_range(rng_c, "2026-10-09T09:15:00+05:30"))
lc = [{"t": d2[0][0], "close": 25300.0, "f4": 25200.0, "taken": True, "lot_size": 65,
       "trade": kw.day_trade({"side": "CE", "entry": 25300.0, "target": 25400.0, "stop": 25290.0, "premium": 120.0,
                              "delta": 0.5}, 25450.0, 25290.0, 100.0)}]
kw.score_days(lc, cand)
check("a ladder trade is scored the moment the candles show it out - today's falling session stops the call at "
      "25250 during the day, not after the close", lc[0]["result"] == "stop" and lc[0]["points"] == -50.0
      and lc[0].get("actual") is None and lc[0]["exit_t"].startswith("2026-10-13T"), lc[0])
dp_ = kw.day_trade({"side": "PE", "entry": 25000.0, "target": 24950.0, "stop": 25010.0}, 25010.0, 24930.0)
check("...a put's target its forecast LOW", dp_["target"] == 24930.0 and dp_["t1"] == 24965.0, dp_)
dn_ = kw.day_trade({"side": "CE", "entry": 25000.0, "target": 25080.0, "stop": 24990.0}, None, None)
check("...no day high known (an old record): its forecast close", dn_["target"] == 25080.0, dn_)
c = {"trade": kw.day_trade({"side": "CE", "entry": 25000.0, "target": 25100.0, "stop": 24850.0}, 25150.0, 24850.0)}
kw.decide_day(c)
check("no stop: a far-away old stop no longer matters - traded", c["taken"] is True, c)
c = {"trade": kw.day_trade({"side": "CE", "entry": 25000.0, "target": 25020.0, "stop": 24990.0}, 25200.0, 24990.0)}
kw.decide_day(c)
check("...but a small move TO THE CLOSE is still skipped, however high its day high", c["taken"] is False
      and c["no_trade"] == "a small move", c)
hc = [{"t": d1[0][0], "close": 25000.0, "f4": 25050.0, "taken": True, "lot_size": 65,
       "trade": kw.day_trade({"side": "CE", "entry": 25000.0, "target": 25050.0, "stop": 24999.0, "premium": 120.0,
                              "premium_stop": 119.5, "delta": 0.5}, 25100.0, 24999.0)},
      {"t": d1[0][0], "close": 25000.0, "f4": 24950.0, "taken": True,
       "trade": kw.day_trade({"side": "PE", "entry": 25000.0, "target": 24950.0, "stop": 25001.0}, 25001.0, 24900.0)}]
kw.score_days(hc, cand)
check("a call's target (its day high, 25100) reached on a rising day: out there (+100); a put's (24900) never - out "
      "at the day's last close (-240)", (hc[0]["result"], hc[0]["points"], hc[1]["result"], hc[1]["points"])
      == ("target", 100.0, "close", -240.0) and hc[0]["rupees"] == 100 * 0.5 * 65,
      [(c.get("result"), c.get("points"), c.get("rupees")) for c in hc])

# the worker's own pass, with a stand-in for the model (every forecast rises 6 points a candle) and a short context
real = (kw.BASE, kw.forecast, kw.CONTEXT)
kw.BASE, kw.CONTEXT = kl.base(), 25      # 09 Oct's 09:15 candle is the 26th
asked = []
def fake_forecast(pred, windows, continuous=False, n=kw.PRED_LEN):
    asked.append((len(windows), n))
    out = []
    for w in windows:
        c0 = w[-1][4]
        out.append([[x.isoformat(), c0, c0 + 6 * (k + 1) + 3, c0 + 6 * k - 3, c0 + 6 * (k + 1)]
                    for k, x in enumerate(kw.next_slots(kw.parse(w[-1][0]), n))])
    return out
kw.forecast = fake_forecast
try:
    hist = (session("2026-10-08", 24800, 2) + session("2026-10-09", 24850, 2) + session("2026-10-12", 24900, 2)
            + session("2026-10-13", 25000, 2, n=1))
    chain = {"expiry": "2026-10-14", "strikes": {"25000": {"CE": 130.0, "PE": 110.0}}}
    inp = {"index": "NIFTY", "market": "nse_index", "candles": hist, "tool": {"dir": 1}, "strike_step": 50,
           "lot_size": 65, "delta": 0.5, "chain": chain}
    ipath = os.path.join(kw.BASE, "in", "NIFTY.json")
    kw._write(ipath, inp)
    dpath = os.path.join(kw.BASE, "days", "NIFTY.json")
    kw._day(None, ipath, False, now)
    days = json.load(open(dpath))
    lv0 = days[-1] if days else {}
    check("at 09:30 a live whole-day call from today's 09:15 candle: 24 candles ahead, in the option at the chain's "
          "price, the tool's direction then", asked == [(1, 24)] and len(days) == 1 and lv0["t"] == hist[75][0]
          and not lv0["replay"] and lv0["trade"]["premium"] == 130.0 and lv0["trade"]["strike"] == 25000
          and lv0["tool_dir"] == 1 and lv0["kind"] == "day" and lv0["taken"] is True and len(lv0["path"]) == 24,
          (asked, {k: lv0.get(k) for k in ("t", "replay", "taken", "tool_dir", "trade")}))
    check("...its target its forecast high for the day (25147), its forecast close (+144) kept, its day's high and "
          "low kept", lv0["trade"]["target"] == 25147.0 and lv0["trade"]["close_fc"] == 25144.0 and lv0["hi"] == 25147.0
          and lv0["lo"] == 24997.0, (lv0["trade"], lv0.get("hi"), lv0.get("lo")))
    check("...a ladder, the option's target premium by delta (130 + 147 x 0.5); three sessions of history - fewer "
          "than the 10 a day range needs - so no stop", lv0["trade"]["exit"] == "ladder"
          and lv0["trade"]["stop"] is None and lv0["trade"]["premium_target"] == 203.5, lv0["trade"])
    kw._day(None, ipath, False, now)
    check("made once: the next pass forecasts nothing", asked == [(1, 24)], asked)
    kw._day(None, ipath, True, now)
    days = json.load(open(dpath))
    reps = [c for c in days if c.get("replay")]
    check("the replay: the finished sessions with a full context before them (09 and 12 Oct, not 08), in index points "
          "only, scored", [c["t"][:10] for c in reps] == ["2026-10-09", "2026-10-12"] and asked[-1] == (2, 24)
          and all("premium" not in c["trade"] and c.get("actual") is not None for c in reps),
          [(c["t"][:10], c.get("actual"), c.get("result")) for c in reps])
    kw._day(None, ipath, True, now)
    check("...replayed once only", len(asked) == 2, asked)
    # a record made under the first exit (its forecast's own target and stop) is re-decided and re-scored as held to
    # the close on the next pass - no new forecast
    days = json.load(open(dpath))
    old = [c for c in days if c.get("replay")][-1]
    old["trade"] = dict(old["trade"], stop=old["close"] - 3.0, premium_stop=1.0)
    old["trade"].pop("exit")
    old.update(result="stop", points=-3.0, taken=True)
    kw._write(dpath, days)
    kw._day(None, ipath, False, now)
    again = [c for c in json.load(open(dpath)) if c["t"] == old["t"]][0]
    sess = [x for x in hist if x[0][:10] == old["t"][:10]][1:]
    up = again["trade"]["side"] == "CE"
    P = old["hi"] if up else old["lo"]
    res_, lvl_, _, _ = kw.ladder_walk(again["trade"], sess)
    want_r, want_p = (res_, round((lvl_ - old["close"]) * (1 if up else -1), 2)) if res_ else \
        ("close", round((sess[-1][4] - old["close"]) * (1 if up else -1), 2))
    check("a day recorded under an earlier exit is re-scored under this one - the ladder to its day high - with no "
          "new forecast", len(asked) == 2 and again["trade"]["exit"] == "ladder" and again["trade"]["target"] == P
          and (again["result"], again["points"]) == (want_r, want_p),
          (again.get("result"), again.get("points"), want_r, want_p))
    # the worker reached today late: the 09:30 candle had closed too - no option price from 09:15, no tool direction
    os.remove(dpath)
    kw._write(ipath, dict(inp, candles=hist + session("2026-10-13", 25000, 2, n=2)[1:]))
    kw._day(None, ipath, False, now)
    late = json.load(open(dpath))[-1]
    check("made late: still from the 09:15 candle, but in index points and without the tool's direction now",
          late["late"] is True and "premium" not in late["trade"] and late["tool_dir"] is None
          and late["t"] == hist[75][0], {k: late.get(k) for k in ("late", "tool_dir", "trade")})
    # first seen at night, today's session already over: no "live" call from it - the chain and the tool's direction
    # sent now are not 09:30's (the replay covers it, in points)
    os.remove(dpath)
    kw._write(ipath, dict(inp, candles=hist[:75] + session("2026-10-13", 25000, 2)))
    n0 = len(asked)
    kw._day(None, ipath, False, now)
    check("first seen after the close: no live call from a finished session", len(asked) == n0
          and not os.path.exists(dpath), asked[n0:])
    kw._write(ipath, inp)
    kw._day(None, ipath, False, now)
    kw._day(None, ipath, True, now)
    bpath = os.path.join(kw.BASE, "in", "BTCX.json")
    kw._write(bpath, dict(inp, market="crypto", index="BTCX"))
    n0 = len(asked)
    kw._day(None, bpath, False, now)
    check("no whole day on crypto - it never closes", len(asked) == n0 and not os.path.exists(os.path.join(kw.BASE, "days", "BTCX.json")))
    os.remove(bpath)
    pub = kl.public("nse_index", now)
    nf = [i for i in pub["indices"] if i["index"] == "NIFTY"][0]
    check("the tab gets the day on screen - today's live call with its whole path - its record and the days listed, "
          "live first", nf["day"]["t"] == hist[75][0] and not nf["day"]["replay"] and len(nf["day"]["path"]) == 24
          and nf["days"][0]["t"] == hist[75][0] and nf["day_score"]["replay"]["checked"] == 2
          and nf["day_score"]["live"]["calls"] == 1, (nf["day"] and nf["day"]["t"], nf["day_score"]["replay"]))
finally:
    kw.BASE, kw.forecast, kw.CONTEXT = real

print("4g. ITS TRADES TODAY, WITH THE OPTION'S PRICE NOW (the user: \"entry ltp and present ltp ... moving live\", "
      "\"show it in postions\")")
cpath = os.path.join(kl.base(), "calls", "NIFTY.json")
hour_closed = {"t": "2026-10-13T10:00:00+05:30", "close": 25010.0, "f4": 25060.0, "taken": True, "replay": False,
               "result": "target", "points": 50.0, "rupees": 1625.0, "lot_size": 65,
               "trade": {"side": "CE", "strike": 25000, "entry": 25010.0, "target": 25060.0, "stop": 24990.0,
                         "premium": 140.0, "premium_target": 165.0, "premium_stop": 130.0}}
kw._write(cpath, [hour_closed,
                  dict(hour_closed, t="2026-10-13T10:30:00+05:30", taken=False, no_trade="a small move", result=None),
                  dict(hour_closed, t="2026-10-13T10:15:00+05:30", replay=True),
                  dict(hour_closed, t="2026-10-12T14:00:00+05:30")])


class LiveFeed:
    def __init__(self, ltp=None, spot=None):
        self.ltp, self.spot = ltp, spot
    def ticks(self):
        return {"spots": {"NIFTY": self.spot, "BTC": self.spot}}
    def contract_quote(self, name, strike, side, expiry=None):
        ok = (name, float(strike), side, expiry) == ("NIFTY", 25000.0, "CE", "2026-10-14")
        return {"ltp": self.ltp} if ok and self.ltp is not None else None


lv = kl.live("nse_index", LiveFeed(152.5, 25060.0), now)
rows = [r for r in lv["trades"] if r["index"] == "NIFTY"]
op = [r for r in rows if r["open"]]
check("today's open day trade: the option's price NOW and its P&L for a lot ((152.5 - 130) x 65), the index now, open "
      "till the close", len(op) == 1 and op[0]["kind"] == "day" and op[0]["ltp"] == 152.5
      and op[0]["pnl"] == round(22.5 * 65, 2) and op[0]["spot"] == 25060.0 and op[0]["ends"].endswith("15:30:00+05:30"),
      op)
cl = [r for r in rows if not r["open"]]
check("today's finished hour trade: its result and estimated rupees, no price asked; open ones listed first",
      len(cl) == 1 and cl[0]["kind"] == "hour" and cl[0]["result"] == "target" and cl[0]["rupees"] == 1625.0
      and "ltp" not in cl[0] and rows.index(op[0]) < rows.index(cl[0]), cl)
check("not listed: a call it did not trade, a replay, another day's trade",
      len(rows) == 2 and all(r["t"].startswith("2026-10-13") for r in rows), [(r["t"], r["kind"]) for r in rows])
check("an hour trade ends an hour after its own candle closes", cl[0]["ends"] == "2026-10-13T11:15:00+05:30", cl[0]["ends"])
nofeed = [r for r in kl.live("nse_index", None, now)["trades"] if r["open"]]
check("with no feed it is still listed, without a price - nothing breaks", nofeed and nofeed[0]["ltp"] is None
      and "pnl" not in nofeed[0], nofeed)
kw._write(os.path.join(kl.base(), "calls", "BTC.json"),
          [{"t": "2026-10-13T10:00:00+05:30", "close": 82000.0, "f4": 81800.0, "taken": True, "replay": False,
            "lot_size": 1.0, "trade": {"side": "PE", "entry": 82000.0, "target": 81800.0, "stop": 82100.0, "cfd": True,
                                       "strike": None}}])
kw._write(os.path.join(kl.base(), "days", "SENSEX.json"),
          [{"t": "2026-10-13T09:15:00+05:30", "close": 25000.0, "f4": 25080.0, "taken": True, "replay": False,
            "lot_size": 20, "trade": kw.day_trade({"side": "CE", "strike": 25000, "entry": 25000.0, "target": 25080.0,
                                                   "stop": 24990.0, "premium": 130.0, "delta": 0.5}, 25120.0, 24990.0, 200.0)}])
kw._write(os.path.join(kl.base(), "in", "SENSEX.json"),
          {"candles": [["2026-10-13T09:15:00+05:30", 0, 25005, 24995, 25000.0],
                       ["2026-10-13T09:30:00+05:30", 0, 25070, 25010, 25060.0]]})


class SensexFeed(LiveFeed):
    def ticks(self):
        return {"spots": {"SENSEX": 25095.0}}


sx = [r for r in kl.live("nse_index", SensexFeed(), now)["trades"] if r["index"] == "SENSEX"]
check("live: a ladder trade's stop where it is NOW - T1 touched by a closed candle, T2 by the live price -> T1 "
      "(25060), its option price 130 + 30 = 160", len(sx) == 1 and sx[0]["stop_now"] == 25060.0
      and sx[0]["premium_stop_now"] == 160.0 and sx[0]["open"], sx)
cv = kl.live("crypto", LiveFeed(None, 81950.0), now)["trades"]
check("a CFD sell: the price now, and P&L for 1 BTC the right way round (82000 - 81950 = +50)",
      len(cv) == 1 and cv[0]["cfd"] and cv[0]["spot"] == 81950.0 and cv[0]["pnl"] == 50.0, cv)

print("5. THE PAGE, AND THE TRADING NEVER LOADS IT")
SRC = open(os.path.join(HERE, "web_server.py")).read()
check("a Kronos tab under Research, and its pane", 'data-tab="kronos"' in SRC.split('data-grp="research"')[1].split("</div>")[0]
      and '<section class="pane" data-pane="kronos">' in SRC)
check("its route and handler", 'if path == "/api/kronos":' in SRC and "def _api_kronos(self, user):" in SRC)
check("one index at a time, like the Signal tab: the tool's own index pick redraws it, its switcher sets it",
      'if(TAB === "kronos" && typeof KR !== "undefined" && KR.data) kronosDraw(KR.data);' in SRC
      and 'const ix = rows.find(r => r.index === CUR) || rows[0];' in SRC and "if(b) selectIndex(b.dataset.k);" in SRC)
check("its sections: the switcher, the call, the trade, the chart, the record, all four",
      all(f'id="{i}"' in SRC for i in ("kronosidx", "kcallcard", "ktrade", "kchartcard", "krecard", "kallcard", "kcalls")))
check("the trade drawn with the tool's own ticket panel", "+ posPanel(head, rowsT, \"k4\")" in SRC)
check("...its option's price at entry and NOW, then its target and stop",
      '[["Entry LTP", "₹" + num(t.premium, 2)],' in SRC and '["LTP now", ltp == null' in SRC
      and '["Target", "₹" + num(t.premium_target, 2), "var(--up)"], ["Stop", "₹" + num(t.premium_stop, 2), "var(--down)"]]' in SRC)
check("the whole day: its card, trade, chart and record", all(f'id="{i}"' in SRC for i in
      ("kdaycard", "kddir", "kdtrade", "kdchart", "kdstats", "kdcalls", "kdnote")))
check("the day trade's target AND stop in the OPTION's price, the stop where the ladder has moved it (the user: "
      "\"its not giving a stop loss and targets should be shown in option prices not index price\")",
      '["Stop", lad ? (stopPrem == null ? "—" : "₹" + num(stopPrem, 2)) : "none", lad ? "var(--down)" : ""]]' in SRC
      and "const stopPrem = lad ? (isOpen && lv.premium_stop_now != null ? lv.premium_stop_now : t.premium_stop) : null;" in SRC
      and "const stp = cfd ? t.stop : (r.premium_stop_now != null ? r.premium_stop_now : t.premium_stop);" in SRC)
check("...at the lots picked on its card (kept on the device, never the tool's own trading lots): the cost, what the "
      "target is worth before and after charges", 'localStorage.setItem("kronos.lots." + e.target.dataset.k' in SRC
      and '["Cost (entry LTP × qty)"' in SRC and '["At target, after charges"' in SRC
      and 'id="kdlots"' in SRC and "setLots" not in SRC.split("function krLots(")[1].split("\nfunction ")[0])
check("...and its ladder, as the Signal tab's: 50%, 75%, the target and the stop, each with its level, the option's "
      "price there and its worth at those lots", 'id="kdladder"' in SRC and "function krLadder(ix, day, lv)" in SRC
      and '[lad ? "T1 · halfway" : "50% of the way", t.t1, t.premium_t1]' in SRC
      and '<div class="n" style="color:var(--up)">${pv == null ? num(v, 0) : "₹" + num(pv, 2)}</div>' in SRC)
check("its live route and handler, off the user's own feed without starting one",
      'if path == "/api/kronos/live":' in SRC and "feeds.for_user(user, market, start=False)" in SRC)
check("polled every few seconds while the Kronos tab or Positions is on screen",
      'if((TAB === "kronos" || TAB === "positions") && !document.hidden) kronosLiveFetch(); }, 3000);' in SRC)
pos = SRC.split('<section class="pane" data-pane="positions">')[1].split("</section>")[0]
check("on Positions: its own card, apart from the tool's positions, saying research only and not in their P&L",
      'id="kronposcard"' in pos and "No order is placed for them and they are" in pos and "not part of the P&amp;L above" in pos)
pt = SRC.split("function posTable(s){")[1].split("\nfunction ")[0]
pp = SRC.split("function posPane(s){")[1].split("\nfunction ")[0]
check("...and never in the tool's own positions table or its totals", "KR" not in pt and "kronos" not in pt.lower()
      and "KR." not in pp and "kronos" not in pp.lower())
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
