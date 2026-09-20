#!/usr/bin/env python3
"""Futures open interest, build-up and order flow for the bot - the streamer's
book, the build-up labels, finding the futures once a day, the 15-minute
window, the heavyweights, and the feed handing it all to the bot. Fakes only:
nothing here reaches Zerodha."""
import datetime as dt
import json
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()           # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data_providers as dp
import feeds
import kite_flow as kf
import market_bot

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. BUILD-UP LABELS")
check("price up, OI up = long build-up", kf.buildup(0.5, 2.0, 0.1, 1.0) == "long build-up")
check("price down, OI up = short build-up", kf.buildup(-0.5, 2.0, 0.1, 1.0) == "short build-up")
check("price up, OI down = short covering", kf.buildup(0.5, -2.0, 0.1, 1.0) == "short covering")
check("price down, OI down = long unwinding", kf.buildup(-0.5, -2.0, 0.1, 1.0) == "long unwinding")
check("a move under the threshold is no clear build-up, not forced into one",
      kf.buildup(0.05, 5.0, 0.1, 1.0) == "no clear build-up" and kf.buildup(1.0, 0.5, 0.1, 1.0) == "no clear build-up")
check("a missing number is no label at all", kf.buildup(None, 2.0, 0.1, 1.0) is None)

print("2. THE STREAMER'S BOOK CARRIES THE ORDER FLOW")
s = dp.KiteStreamer("k", "t")
s._full_tokens.add(7)
with s._lock:
    s._record_book([{"instrument_token": 7, "last_price": 101.0, "oi": 9000, "average_traded_price": 100.0,
                     "volume_traded": 125000, "total_buy_quantity": 60000, "total_sell_quantity": 40000,
                     "oi_day_high": 9500, "oi_day_low": 8000, "ohlc": {"close": 98.0},
                     "depth": {"buy": [{"price": 100.9, "quantity": 300}, {"price": 100.8, "quantity": 200},
                                       {"price": 0, "quantity": 0}],
                               "sell": [{"price": 101.1, "quantity": 100}]}}], 1000.0)
b = s.book(7)
check("vwap, volume, buy/sell quantity, OI range and the previous close are kept",
      b["vwap"] == 100.0 and b["volume"] == 125000 and b["buy_qty"] == 60000 and b["sell_qty"] == 40000
      and b["oi_high"] == 9500 and b["oi_low"] == 8000 and b["prev_close"] == 98.0, b)
check("the five-level book is summed each side (padded levels add nothing)",
      b["depth_bid_qty"] == 500 and b["depth_ask_qty"] == 100, b)
with s._lock:
    s._record_book([{"instrument_token": 7, "last_price": 101.5}], 1001.0)
check("an LTP-only tick keeps the last order flow", s.book(7)["vwap"] == 100.0 and s.book(7)["ltp"] == 101.5)
f = kf.order_flow(s.book(7))
check("order_flow: price against VWAP, buy-to-sell, the book's lean",
      f["vs_vwap_pct"] == 1.5 and f["buy_to_sell"] == 1.5 and f["book_imbalance"] == round(400 / 600, 2), f)
check("order_flow of nothing is None", kf.order_flow(None) is None)
check("order_flow leaves out what it does not have", "vwap" not in kf.order_flow({"ltp": 5.0}))

print("3. FINDING A NEAR-MONTH FUTURE")
class FakeKite:
    def __init__(self):
        self.dumps = []
    def instruments(self, exch):
        self.dumps.append(exch)
        today = dp._now_ist_naive().date()
        rows = [{"name": "NIFTY", "instrument_type": "FUT", "expiry": today - dt.timedelta(days=3),
                 "instrument_token": 1, "tradingsymbol": "NIFTYOLDFUT"},
                {"name": "NIFTY", "instrument_type": "FUT", "expiry": today + dt.timedelta(days=40),
                 "instrument_token": 3, "tradingsymbol": "NIFTYNEXTFUT"},
                {"name": "NIFTY", "instrument_type": "FUT", "expiry": today + dt.timedelta(days=5),
                 "instrument_token": 2, "tradingsymbol": "NIFTYNEARFUT"},
                {"name": "NIFTY", "instrument_type": "CE", "expiry": today, "instrument_token": 9},
                {"name": "RELIANCE", "instrument_type": "FUT", "expiry": today + dt.timedelta(days=5),
                 "instrument_token": 4, "tradingsymbol": "RELIANCENEARFUT"}]
        return rows if exch == "NFO" else []
p = object.__new__(dp.KiteDataProvider)
p.kite = FakeKite()
dp._FUT_ROWS.clear()
r = p.near_future("NFO", "NIFTY")
check("the nearest contract that has not expired", r and r["instrument_token"] == 2, r)
check("a stock future too", p.near_future("NFO", "RELIANCE")["instrument_token"] == 4)
check("one download of the exchange list a day, kept to futures",
      p.kite.dumps == ["NFO"] and all(x["instrument_type"] == "FUT" for x in list(dp._FUT_ROWS.values())[0]))
check("none on that exchange: None", p.near_future("BFO", "SENSEX") is None)

print("4. THE FLOW: FOUND ONCE A DAY, STREAMED IN FULL")
DAY = {"v": "2026-09-18"}
T = {"v": 10_000.0}


class Provider:
    calls = []
    fail = False
    def near_future(self, exch, name):
        Provider.calls.append(("fut", exch, name))
        if Provider.fail:
            raise RuntimeError("instrument dump timed out")
        base = {"NIFTY": 100, "BANKNIFTY": 200, "SENSEX": 300}.get(name)
        if base is None:
            base = 1000 + sum(map(ord, name))
        return {"instrument_token": base, "tradingsymbol": f"{name}26SEPFUT", "expiry": "2026-09-29"}
    def futures_oi_history(self, tok):
        Provider.calls.append(("hist", tok))
        return [{"date": "2026-09-17 15:15:00+05:30", "close": 25000.0, "oi": 10000},
                {"date": "2026-09-18 09:15:00+05:30", "close": 25100.0, "oi": 10200}]


class Streamer:
    def __init__(self):
        self.subs, self.books = [], {}
    def subscribe(self, toks, quote=False, full=False):
        self.subs.append((sorted(toks), full))
    def book(self, tok, max_age=None):
        return self.books.get(tok)


fl = kf.Flow(["NIFTY", "BANKNIFTY", "SENSEX"], lambda: Provider(), clock=lambda: T["v"],
             today=lambda: DAY["v"], background=False)
st = Streamer()
fl.ensure(st)
nifty = fl.by_index.get("NIFTY") or {}
check("each index's own future", nifty.get("future") == 100 and fl.by_index["SENSEX"]["future"] == 300)
check("the five heaviest members of each index, with their weight",
      [h[0] for h in nifty.get("heavy", [])] == ["HDFCBANK", "ICICIBANK", "RELIANCE", "INFY", "BHARTIARTL"]
      and nifty["heavy"][0][1] == 13.3, nifty.get("heavy"))
check("Sensex's future on BFO, the stock futures on NFO",
      ("fut", "BFO", "SENSEX") in Provider.calls and ("fut", "NFO", "HDFCBANK") in Provider.calls
      and ("fut", "BFO", "HDFCBANK") not in Provider.calls)
check("yesterday's close and OI come from the last bar before today",
      fl.prev[100] == {"close": 25000.0, "oi": 10000}, fl.prev.get(100))
check("every contract put on the socket once, in FULL mode",
      len(st.subs) == 1 and st.subs[0][1] is True and set(st.subs[0][0]) == set(fl.contracts), st.subs)
n = len(Provider.calls)
fl.ensure(st)
check("the next loop costs nothing: no lookup, no second subscribe", len(Provider.calls) == n and len(st.subs) == 1)
st2 = Streamer()
fl.ensure(st2)
check("a rebuilt socket is given them again", len(st2.subs) == 1 and len(Provider.calls) == n)
DAY["v"] = "2026-09-19"
T["v"] += kf.RETRY_S
fl.ensure(st2)
check("a new day looks them up again (a new month's contract, new previous close)",
      len(Provider.calls) > n and fl.day == "2026-09-19")

fl2 = kf.Flow(["NIFTY"], lambda: Provider(), clock=lambda: T["v"], today=lambda: DAY["v"], background=False)
Provider.fail = True
fl2.ensure(st)
m = len(Provider.calls)
fl2.ensure(st)
check("a failed lookup is not retried at tick rate", len(Provider.calls) == m and "timed out" in (fl2.error or ""))
Provider.fail = False
T["v"] += kf.RETRY_S
fl2.ensure(st)
check("...and is retried after RETRY_S", fl2.by_index.get("NIFTY", {}).get("future") == 100)
fl3 = kf.Flow(["NIFTY"], lambda: None, clock=lambda: T["v"], today=lambda: DAY["v"], background=False)
fl3.ensure(st)
check("no Zerodha session: nothing looked up, and it says why", not fl3.contracts and fl3.error == "no Zerodha session")

print("5. THE READING: DAY AND 15-MINUTE BUILD-UP, BASIS, HEAVYWEIGHTS")
DAY["v"] = "2026-09-18"
fl = kf.Flow(["NIFTY"], lambda: Provider(), clock=lambda: T["v"], today=lambda: DAY["v"], background=False)
st = Streamer()
fl.ensure(st)
check("before any tick: known contract, not live", fl.reading("NIFTY", st)["index_future"] == {
    "contract": "NIFTY26SEPFUT", "live": False})
st.books[100] = {"ltp": 25100.0, "oi": 10500, "prev_close": 24950.0, "vwap": 25080.0, "at": T["v"]}
for sym, w, tok in fl.by_index["NIFTY"]["heavy"]:
    st.books[tok] = {"ltp": 99.0 if sym == "RELIANCE" else 101.0, "oi": 1100, "prev_close": 100.0}
fl.prev.update({tok: {"close": 100.0, "oi": 1000} for _s, _w, tok in fl.by_index["NIFTY"]["heavy"]})
r = fl.reading("NIFTY", st, spot=25040.0)
fut = r["index_future"]
check("the day's change is against Zerodha's previous close, OI against yesterday's",
      fut["day_change_pct"] == round(150 / 24950 * 100, 3) and fut["oi_change_pct_since_yesterday"] == 5.0, fut)
check("price up + OI up on the day = long build-up", fut["buildup_today"] == "long build-up")
check("basis: the future over the index, in points and %", fut["basis_points"] == 60.0
      and fut["basis_pct"] == round(60 / 25040 * 100, 3), fut)
check("the future's own order flow rides along", fut["order_flow"]["vwap"] == 25080.0)
check("no 15-minute figure until the feed has run that long", "last_15m" not in fut)
hv = {h["symbol"]: h for h in r["heavyweights"]}
check("heavyweights: each with its weight and build-up",
      hv["HDFCBANK"]["buildup_today"] == "long build-up" and hv["HDFCBANK"]["index_weight_pct"] == 13.3
      and hv["RELIANCE"]["buildup_today"] == "short build-up" and hv["RELIANCE"]["lean"] == "bearish", hv["RELIANCE"])
lean = r["heavyweights_weight_pct"]
check("how much of the heavyweights' weight leans each way",
      lean["bearish_buildup"] == 8.2 and lean["bullish_buildup"] == round(13.3 + 8.9 + 5.1 + 4.5, 1)
      and lean["of_total"] == 40.0, lean)
check("heavyweights stay small - no order flow for each stock", "order_flow" not in hv["INFY"])

t0 = T["v"]
fl.sample(st, market_open=lambda: True)
T["v"] += kf.SAMPLE_S - 1
st.books[100] = dict(st.books[100], ltp=25200.0, oi=10400)
fl.sample(st, market_open=lambda: True)
check("one sample per SAMPLE_S, not per tick", len(fl.samples[100]) == 1)
asked = []
T["v"] += 2
fl.sample(st, market_open=lambda: asked.append(1) or False)
check("no samples while the market is shut", len(fl.samples[100]) == 1 and asked == [1])
T["v"] = t0 + 300
fl.sample(st, market_open=lambda: True)
check("five minutes in: still no 15-minute figure", "last_15m" not in fl.reading("NIFTY", st)["index_future"])
T["v"] = t0 + kf.WINDOW_S + 5
st.books[100] = dict(st.books[100], ltp=24900.0, oi=10700)
fut = fl.reading("NIFTY", st, spot=24850.0)["index_future"]
w = fut.get("last_15m") or {}
check("after 15 minutes: the change over the window and its build-up",
      w.get("price_change_pct") == round(-200 / 25100 * 100, 3) and w.get("oi_change_pct") == round(200 / 10500 * 100, 3)
      and w.get("buildup") == "short build-up" and w.get("minutes") == 15, w)
DAY["v"] = "2026-09-19"
check("yesterday's contracts are not read as today's", fl.reading("NIFTY", st) is None)
DAY["v"] = "2026-09-18"
check("an index it does not cover: None", fl.reading("BTC", st) is None)

print("6. THE FEED HANDS IT TO THE BOT")
feeds.user_kite.token_for = lambda email: "tok"
f = feeds.Feed("t:flow", "flow@example.invalid", "nse_index")
check("an Indian-indices feed has a flow; a crypto feed does not",
      isinstance(f.flow, kf.Flow) and feeds.Feed("t:c", "c@example.invalid", "crypto").flow is None)
f.flow = fl
st.books[555] = {"ltp": 120.0, "vwap": 118.0, "buy_qty": 3000, "sell_qty": 1500, "at": T["v"]}
st.books[777] = {"ltp": 90.0, "vwap": 95.0, "at": T["v"]}
f.streamer = st
f.sug_tokens["NIFTY"] = (25000, "CE", 555)
f.opt_tokens["NIFTY"] = 777
f.spots["NIFTY"] = 24850.0
got = f.flow_readings()["NIFTY"]
check("the suggested contract's order flow, named", got["suggested_contract"]["contract"] == "25000 CE"
      and got["suggested_contract"]["buy_to_sell"] == 2.0, got.get("suggested_contract"))
check("the open ticket's contract's order flow", got["open_ticket_contract"]["vs_vwap_pct"] == round(-5 / 95 * 100, 3))
check("the index future with its basis to the streamed spot", got["index_future"]["basis_points"] == 50.0)
snap = f.snapshot()
check("the snapshot carries it", snap["flow"]["NIFTY"]["index_future"]["contract"] == "NIFTY26SEPFUT")
ctx = json.loads(market_bot.build_context(snap, "nse_index", "NIFTY"))
check("and the bot's context has the selected index's", ctx["futures_and_order_flow"]["index_future"]["price"] == 24900.0
      and "heavyweights" in ctx["futures_and_order_flow"])
check("the bot is told what it means and that it is context, not a signal",
      "futures_and_order_flow" in market_bot.SYSTEM and "never a signal on its own" in market_bot.SYSTEM
      and "futures_and_order_flow" in market_bot.DESK_SYSTEM)
f.streamer = feeds._NO_STREAM
check("no Zerodha socket (crypto): no flow, no error", f.flow_readings() == {})
f.streamer = None

print("7. CONTRACTS WHOSE ORDER FLOW IS READ ARE STREAMED IN FULL")
SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "feeds.py")).read()
ASRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_desk.py")).read()
check("the suggestion and the ticket's contract are FULL", SRC.count("self.streamer.subscribe([tok], full=True)") == 2)
check("the AI desk's ticket contract is FULL", "st.subscribe([tok], full=True)" in ASRC)
check("a rebuilt socket puts them back in FULL, the AI desk's tickets included",
      "new.subscribe([t for t in full if isinstance(t, int)], full=True)" in SRC
      and 'getattr(getattr(self, "ai", None), "_tok", {})' in SRC)
check("the tick loop keeps the futures streaming", "self._flow_tick(st)" in SRC)

print("KITE FLOW TEST PASSED" if not fails else f"KITE FLOW TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
