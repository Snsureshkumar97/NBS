#!/usr/bin/env python3
"""Gold (tokenised gold, XAUT) from Delta Exchange India, beside Bitcoin: its
own spread limit and settlement hour, the provider on a fake Delta that serves
both assets, the ticket gate, the chain payload, the Greeks tab, the Gann label,
the instruction texts - and above all that no real order can ever be sent for
gold. Fakes only: nothing here reaches Delta or a real account."""
import datetime as dt
import json
import os
import sys
import tempfile
import time

import pandas as pd

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()           # nothing touches the real logs
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config
config.ENABLE_CRYPTO = True
config.INSTRUMENTS["GOLD"]["enabled"] = True      # gold is switched off by default (22 Sep 2026); this test is about it (gold_off_test.py covers the off state)
import delta_orders
import delta_provider as dpv
import feeds
import gann
import market_bot
import tickets
import web_server

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


G, B = config.INSTRUMENTS["GOLD"], config.INSTRUMENTS["BTC"]

print("1. GOLD IS A SECOND INSTRUMENT OF THE DELTA MARKET, WITH ITS OWN SETTINGS")
check("the crypto market has Bitcoin first (the default screen) and gold", config.instruments_in("crypto") == ["BTC", "GOLD"])
check("it is Delta's tokenised gold: the XAUT perpetual for candles, the .DEXAUTUSD index for the price, XAUT options",
      (G["provider"], G["delta_perpetual"], G["delta_index"], G["delta_asset"], G["market"]) == ("delta", "XAUTUSD", ".DEXAUTUSD", "XAUT", "crypto"))
check("strikes are $10 apart, and one lot is 100 contracts of 0.001 XAUT (0.1 of the underlying)",
      G["strike_step"] == 10 and G["lot_size"] == 0.1 and G["contracts_per_lot"] == 100 and B.get("contracts_per_lot") is None)
check("the lots selector is the market's own, unchanged by gold", config.lot_choices("crypto") == [float(x) for x in B["lot_choices"]]
      and "lot_choices" not in G)
check("gold's price and index symbol come from the venue's naming", config.crypto_index("GOLD") == ".DEXAUTUSD" and config.crypto_index("BTC") == ".DEXBTUSD")
saved = config.MAX_SPREAD_PCT
check("gold has its own spread limit, 8%; Bitcoin and the Indian indices keep the global 3%",
      config.max_spread_pct("GOLD") == 8.0 and config.max_spread_pct("BTC") == saved == 3.0 and config.max_spread_pct("NIFTY") == 3.0
      and config.max_spread_pct("NOPE") == 3.0 and config.max_spread_pct(None) == 3.0)
config.MAX_SPREAD_PCT = 5.0
check("...and the global still moves every instrument without its own, never gold's",
      config.max_spread_pct("BTC") == 5.0 and config.max_spread_pct("NIFTY") == 5.0 and config.max_spread_pct("GOLD") == 8.0)
config.MAX_SPREAD_PCT = saved
check("options settle at 17:30 IST for Bitcoin (12:00 UTC) and 21:30 IST for gold (16:00 UTC)",
      config.crypto_settle_time("BTC").strftime("%H:%M") == "17:30" and config.crypto_settle_time("GOLD").strftime("%H:%M") == "21:30")
config.INSTRUMENTS["_DERIBIT"] = {"provider": "deribit", "market": "crypto"}
check("a Deribit instrument still settles at 13:30 IST", config.crypto_settle_time("_DERIBIT").strftime("%H:%M") == "13:30")
del config.INSTRUMENTS["_DERIBIT"]

print("2. THE PROVIDER, ON A FAKE DELTA THAT SERVES BOTH ASSETS")
TODAY = dt.datetime.now(dt.timezone.utc).date()
def iso(days): return (TODAY + dt.timedelta(days=days)).isoformat()
def sym(asset, kind, k, days): return f"{kind}-{asset}-{k}-{dpv.tag_of(iso(days))}"


class Resp:
    def __init__(self, body): self._body = body
    def raise_for_status(self): pass
    def json(self): return self._body


SPREADS = {"XAUT": {0: 5.0, 2: 12.0, 3: 6.0, 5: 5.0}, "BTC": {0: 6.0, 2: 6.0, 3: 2.0, 5: 1.5}}
STRIKES = {"XAUT": (4340, 4350, 4360, 4370, 4380, 4390, 4400), "BTC": (80000, 80400, 80800, 81200)}
SPOT = {"XAUT": 4371.0, "BTC": 80500.0}


class FakeHTTP:
    def __init__(self): self.calls = []
    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        p = params or {}
        if url.endswith("/v2/history/candles"):
            end = int(p["end"]); step = 900
            ts = list(range(end - end % step, max(int(p["start"]), end - step * 20), -step))
            base = 4371.0 if p["symbol"] == "XAUTUSD" else 80500.0
            return Resp({"success": True, "result": [{"time": t, "open": base, "high": base + 3, "low": base - 3, "close": base + 1, "volume": 9.0} for t in ts]})
        if url.endswith("/v2/tickers/.DEXAUTUSD") or url.endswith("/v2/tickers/.DEXBTUSD"):
            return Resp({"success": True, "result": None})              # Delta answers null at times
        if url.endswith("/v2/tickers/XAUTUSD"):
            return Resp({"success": True, "result": {"spot_price": "4371.0"}})
        if url.endswith("/v2/tickers/BTCUSD"):
            return Resp({"success": True, "result": {"spot_price": "80500.0"}})
        if url.endswith("/v2/tickers"):
            asset = p["underlying_asset_symbols"]; rows = []
            for days, sp in SPREADS[asset].items():
                for k in STRIKES[asset]:
                    for kind in ("C", "P"):
                        mid = 28.0 if asset == "XAUT" else 700.0
                        half = mid * sp / 200.0
                        rows.append({"symbol": sym(asset, kind, k, days), "mark_price": f"{mid:.2f}", "close": mid, "oi": "5.0",
                                     "spot_price": str(SPOT[asset]),
                                     "quotes": {"best_bid": f"{mid - half:.2f}", "best_ask": f"{mid + half:.2f}", "mark_iv": "0.23"},
                                     "greeks": {"delta": "0.5" if kind == "C" else "-0.5", "gamma": "0.001", "theta": "-3.0", "vega": "2.0", "rho": "0.1"}})
            return Resp({"success": True, "result": rows})
        raise AssertionError(url)


fh = FakeHTTP()
p = dpv.DeltaDataProvider(session=fh)
config.MAX_SPREAD_PCT = 3.0
def fresh():
    dpv._PICK.clear(); dpv._PICK_AT.clear(); dpv._SINCE.clear(); dpv._BAD.clear(); p._chain_cache.clear()
fresh()
gold_exp = p.pick_expiry("GOLD")
check("gold's expiry: the nearest with more than a day to run whose spread is under ITS limit (8%) - the 12%-wide one and today's are passed over",
      gold_exp == iso(3), (gold_exp, iso(2), iso(3)))
config.INSTRUMENTS["GOLD"]["max_spread_pct"] = 3.0
fresh()
check("...and it is the per-instrument limit that did it: at the global 3% nothing qualifies and the first live expiry is used instead",
      p.pick_expiry("GOLD") == iso(2))
config.INSTRUMENTS["GOLD"]["max_spread_pct"] = 8.0
fresh()
check("Bitcoin is undisturbed: its expiry is chosen under ITS 3%", p.pick_expiry("BTC") == iso(3))
hl = lambda hour: dpv.hours_left(iso(3), hour)
check("hours to settlement use the instrument's own hour: gold's 16:00 UTC is 4 hours later than Bitcoin's 12:00", abs((hl(16) - hl(12)) - 4.0) < 1e-6
      and abs(hl(None) - hl(12)) < 1e-6)
seen_hours = []
real_hl = dpv.hours_left
dpv.hours_left = lambda expiry, hour=None: (seen_hours.append(hour), real_hl(expiry, hour))[1]
try:
    fresh(); p.pick_expiry("GOLD"); gold_hours = set(seen_hours)
    del seen_hours[:]; fresh(); p.pick_expiry("BTC"); btc_hours = set(seen_hours)
finally:
    dpv.hours_left = real_hl
check("choosing an expiry counts the day's run-out from the instrument's own settlement hour (gold 16, Bitcoin 12), not a shared one",
      gold_hours == {16} and btc_hours == {12}, (gold_hours, btc_hours))
fresh()
check("the chain is only the asset asked for: gold's strikes, none of Bitcoin's",
      [s["strike"] for s in p.get_option_chain("GOLD")["strikes"]] == [4340, 4350, 4360, 4370, 4380, 4390, 4400]
      and [s["strike"] for s in p.get_option_chain("BTC")["strikes"]] == [80000, 80400, 80800, 81200])
ch = p.get_option_chain("GOLD")
check("gold's chain: dollar marks, bid/ask, the expiry the tool uses, the spot from the perpetual", ch["expiry"] == iso(3)
      and ch["spot"] == 4371.0 and ch["strikes"][3]["call_ltp"] == 28.0 and ch["strikes"][3]["call_bid"] < 28.0 < ch["strikes"][3]["call_ask"])
check("Delta's symbol for a gold contract: C-XAUT-<strike>-<DDMMYY>", p.option_instrument("GOLD", 4370, "CE", iso(3)) == sym("XAUT", "C", 4370, 3)
      and p.option_instrument("GOLD", 4375, "CE", iso(3)) is None)
check("the price when the index ticker answers null: the perpetual's spot_price", p.spot("GOLD") == 4371.0 and p.spot("BTC") == 80500.0)
df = p.get_ohlc("GOLD", "15m", 1)
check("gold's candles come from its own perpetual, with volume", any(u.endswith("/history/candles") and c.get("symbol") == "XAUTUSD" for u, c in fh.calls)
      and float(df["Volume"].iloc[-1]) == 9.0)
p.get_ohlc("BTC", "15m", 1)
check("...and Bitcoin's from its own", any(c.get("symbol") == "BTCUSD" for u, c in fh.calls))

print("3. THE TICKET GATE READS THE INSTRUMENT'S LIMIT")
tb = tickets.TicketBook(None, market="crypto")
def rec(index, pct): return {"index": index, "suggested_strike": 4370, "option_type": "CE", "spread": {"pct": pct, "bid": 26.0, "ask": 28.0}}
check("a 6% spread on gold is not held", tb._spread_hold(rec("GOLD", 6.0)) is None)
h = tb._spread_hold(rec("GOLD", 9.0))
check("a 9% spread on gold is held, and says gold's own limit", h and h[0] == "wide_spread" and "under 8%" in h[2], h and h[2][-90:])
h = tb._spread_hold(rec("BTC", 6.0))
check("the same 6% on Bitcoin IS held, under 3%", h and "under 3%" in h[2], h and h[2][-60:])
check("an unknown spread never blocks, gold or not", tb._spread_hold({"index": "GOLD", "spread": {}}) is None)

print("4. THE CHAIN PAYLOAD THE PAGE READS")
try:
    pub = feeds._public({"index": "GOLD", "spot": 4371.0, "bias": "BULLISH", "option_type": "CE", "suggested_strike": 4370,
                         "option_chain": {"expiry": iso(3)}, "technical": {}, "trend": {}}, "GOLD")
    pubb = feeds._public({"index": "BTC", "spot": 80500.0, "bias": "BULLISH", "option_type": "CE", "suggested_strike": 80400,
                          "option_chain": {"expiry": iso(3)}, "technical": {}, "trend": {}}, "BTC")
    check("gold's payload carries its 8% limit and its 100-contract lot; Bitcoin's its 3% and none",
          pub["max_spread"] == 8.0 and pub["contracts_per_lot"] == 100 and pub["lot_size"] == 0.1
          and pubb["max_spread"] == 3.0 and pubb["contracts_per_lot"] is None and pubb["lot_size"] == 0.001, (pub["max_spread"], pub["contracts_per_lot"]))
except Exception as exc:
    check("the chain payload builds for gold", False, f"{type(exc).__name__}: {exc}")

print("5. THE GREEKS TAB AND THE GANN LABEL")
class FakeFeed:
    def _provider(self): return p, "ok", ""
def handler(market="crypto"):
    h = object.__new__(web_server.Handler); out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, code=code)
    h._current_market = lambda: market
    return h, out
orig = feeds.for_user
feeds.for_user = lambda user, market=None, start=True: FakeFeed()
try:
    fresh()
    h, out = handler(); h._api_greeks("me@example.invalid", {"index": ["GOLD"]})
    d = json.loads(out["body"])
    settle16 = dt.datetime(TODAY.year, TODAY.month, TODAY.day, 16, tzinfo=dt.timezone.utc) + dt.timedelta(days=3)
    want = (settle16 - dt.datetime.now(dt.timezone.utc)).total_seconds() / 60.0
    check("the Greeks tab for gold: the venue's own figures for XAUT on the expiry the tool uses", d["index"] == "GOLD" and d["expiry"] == iso(3)
          and d["solved"] == 14 and d["atm_iv"] == 23.0 and d["source"] == "Delta Exchange India", (d.get("expiry"), d.get("solved")))
    check("...counting the minutes to gold's own 16:00 UTC settlement, and saying so in XAUT and IST",
          abs(d["minutes"] - want) < 2 and "16:00 UTC (21:30 IST)" in d["note"] and "1 XAUT" in d["note"] and "0.001 XAUT" in d["note"], (d["minutes"], round(want), d["note"][-90:]))
    fresh()
    h, out = handler(); h._api_greeks("me@example.invalid", {"index": ["BTC"]})
    d = json.loads(out["body"])
    check("Bitcoin's Greeks note is unchanged: 12:00 UTC, 17:30 IST, BTC", "12:00 UTC (17:30 IST)" in d["note"] and "1 BTC" in d["note"])
finally:
    feeds.for_user = orig
idx = pd.date_range("2026-09-21 09:00", periods=40, freq="15min", tz="Asia/Kolkata")
dfv = pd.DataFrame({"Open": 4371.0, "High": 4375.0, "Low": 4367.0, "Close": 4371.0, "Volume": [10.0] * 35 + [30.0] * 5}, index=idx)
check("the volume oscillator's volume is the perpetual's for gold and Bitcoin, the future's for an Indian index",
      gann.report("GOLD", 4371.0, dfv)["volume_oscillator"]["volume_of"] == "the perpetual"
      and gann.report("BTC", 80500.0, dfv)["volume_oscillator"]["volume_of"] == "the perpetual"
      and gann.report("NIFTY", 25000.0, dfv)["volume_oscillator"]["volume_of"] == "the near-month future")

print("6. NO REAL ORDER CAN EVER BE SENT FOR GOLD")
check("the Delta executor's instruments are Bitcoin alone", delta_orders.INDICES == ("BTC",))
tmp = os.path.join(tempfile.mkdtemp(), "trades.csv")
class NoKite:
    def __getattr__(self, n): raise AssertionError("the executor must not call Delta for gold")
ex = delta_orders.Executor("me@example.invalid", tmp, client=lambda: NoKite(), close_ticket=lambda i, s: None, start=False)
ex.on_ticket_event("opened", {"trade_id": "GOLD-1", "index": "GOLD", "strike": 4370, "option_type": "CE", "lots": 10, "use_premium": True,
                              "entry_ltp": 28.0, "premium_sl": 20.0, "premium_targets": [34.0] * 3, "expiry": iso(3), "status": "OPEN"})
ex.on_ticket_event("opened", {"trade_id": "GOLD-2", "index": "GOLD", "status": "OPEN"}, source="ai")
check("a gold ticket, rule or AI, queues nothing for the executor", ex.q.empty() and ex.positions == {})
try:
    ex.set_enabled("GOLD", True); check("gold's live switch cannot be turned on", False)
except ValueError:
    check("gold's live switch cannot be turned on", not ex.enabled.get("GOLD") and not ex.enabled_ai.get("GOLD"))
pub = ex.public()
check("the page is only ever offered Bitcoin's live switches", list(pub["enabled"]) == ["BTC"] and list(pub["enabled_ai"]) == ["BTC"])
import accounts
import user_delta
class Stub: live = ex
saved = (feeds.for_user, user_delta.keys_for, accounts.get_user, web_server._state["mode"])
try:
    feeds.for_user = lambda user, market=None, start=True: Stub()
    user_delta.keys_for = lambda email: ("k", "s")
    accounts.get_user = lambda email: {"always_on": True}
    web_server._state["mode"] = "kite"
    for source in ("rule", "ai"):
        h, out = handler(); h._current_user = lambda: "me@example.invalid"; h._same_origin = lambda: True
        h._do_live({"index": "GOLD", "on": "1", "source": source})
        check(f"the live switch endpoint refuses gold ({source}), keys and always-on notwithstanding",
              out["code"] == 400 and "Bitcoin" in json.loads(out["body"])["message"] and not ex.enabled_ai.get("GOLD") and not ex.enabled.get("GOLD"), out.get("body"))
finally:
    feeds.for_user, user_delta.keys_for, accounts.get_user, web_server._state["mode"] = saved
check("gold is marked paper-only where the executor is defined", "paper-only" in open(os.path.join(HERE, "delta_orders.py")).read().split("INDICES = ")[0][-600:])

print("7. WHAT THE PAGE AND THE AI ARE TOLD")
SRC = open(os.path.join(HERE, "web_server.py")).read()
check("the TradingView tab shows spot gold for gold, and Bitcoin's own for Bitcoin", 'GOLD: "TVC:GOLD"' in SRC and 'BTC: "BITSTAMP:BTCUSD"' in SRC)
check("a lot of 100 contracts reads as Lots, and its title says how many contracts", "const cpl = r.contracts_per_lot || 1;" in SRC
      and "one lot is ${cpl} contracts" in SRC and '(r.contracts_per_lot || 1) > 1) ? "lot" : "contract"' in SRC)
check("the crypto screen says Bitcoin and gold when the market lists gold", '(gold ? "Bitcoin and gold options" : "Bitcoin options")' in SRC
      and '(gold ? "BTC · Gold" : "BTC") + " · Delta Exchange · 24/7"' in SRC and '(s.order || []).includes("GOLD")' in SRC)
check("both instruction texts describe gold - its lot, its spread limit, its settlement, its thin weekends - and that it is always paper",
      all("Gold (GOLD) is tokenised gold" in t and "100 contracts of 0.001 XAUT" in t and "allows up to 8% there" in t
          and "21:30 IST" in t and "thinly at weekends" in t and "Gold tickets are always paper" in t
          for t in (market_bot.SYSTEM, market_bot.DESK_SYSTEM)))

print("GOLD TEST PASSED" if not fails else f"GOLD TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
