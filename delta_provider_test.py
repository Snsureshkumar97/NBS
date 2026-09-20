#!/usr/bin/env python3
"""Bitcoin from Delta Exchange India: the symbols, candles paged newest-first,
the chain in the engine's shape with dollar prices, the sticky expiry, the
socket's messages, and the surface feeds.py relies on. A fake HTTP session and
hand-made socket frames only - nothing here reaches Delta."""
import datetime as dt
import json
import os
import sys
import tempfile
import time

import pandas as pd

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()           # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import delta_provider as dpv

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


BTC = config.INSTRUMENTS["BTC"]
check("config names Delta's contracts for BTC: the perpetual, the index and the asset",
      BTC.get("delta_perpetual") == "BTCUSD" and BTC.get("delta_index") == ".DEXBTUSD" and BTC.get("delta_asset") == "BTC",
      {k: BTC.get(k) for k in ("delta_perpetual", "delta_index", "delta_asset", "provider")})
BTC.setdefault("delta_perpetual", "BTCUSD"); BTC.setdefault("delta_index", ".DEXBTUSD"); BTC.setdefault("delta_asset", "BTC")

print("1. SYMBOLS AND DATES")
check("C-BTC-80000-250926 expires 25 Sep 2026", dpv.expiry_of("C-BTC-80000-250926") == dt.date(2026, 9, 25))
check("an ISO date becomes DDMMYY", dpv.tag_of("2026-09-25") == "250926" and dpv.tag_of("2026-10-02") == "021026")
check("not an option symbol: None", dpv.expiry_of("BTCUSD") is None and dpv.expiry_of("") is None)


class Resp:
    def __init__(self, body):
        self._body = body
    def raise_for_status(self):
        pass
    def json(self):
        return self._body


NOW = int(time.time())
TODAY = dt.datetime.now(dt.timezone.utc).date()
def iso(days):
    return (TODAY + dt.timedelta(days=days)).isoformat()
def sym(kind, strike, exp):
    return f"{kind}-BTC-{strike}-{dpv.tag_of(exp)}"


class FakeHTTP:
    """Answers the public endpoints like Delta does."""
    def __init__(self):
        self.calls = []
        self.spread = {iso(1): 12.0, iso(2): 3.0, iso(9): 2.0}    # ATM spread % per expiry
    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        p = params or {}
        if url.endswith("/v2/history/candles"):
            end, start = int(p["end"]), int(p["start"])
            step = 900
            ts = list(range(end - end % step, start, -step))[:dpv.PAGE]       # newest first, a page at most
            return Resp({"success": True, "result": [{"time": t, "open": 80000.0, "high": 80100.0, "low": 79900.0,
                                                       "close": 80050.0, "volume": 12.5} for t in ts]})
        if url.endswith("/v2/tickers/.DEXBTUSD"):
            return Resp({"success": True, "result": {"symbol": ".DEXBTUSD", "spot_price": "80400.5", "close": 80399.0}})
        if url.endswith("/v2/tickers"):
            rows = []
            for days, sp in self.spread.items():
                for k in (79600, 80000, 80400, 80800, 81200):
                    for kind in ("C", "P"):
                        mid = 900.0 if k == 80000 else 500.0
                        half = mid * sp / 200.0
                        rows.append({"symbol": sym(kind, k, days), "contract_type": "call_options" if kind == "C" else "put_options",
                                     "strike_price": str(k), "mark_price": f"{mid:.2f}", "close": mid - 1,
                                     "oi": "3.5" if kind == "C" else "7.0", "spot_price": "80400.5",
                                     "quotes": {"best_bid": f"{mid - half:.1f}", "best_ask": f"{mid + half:.1f}"}})
            rows.append({"symbol": "C-ETH-4000-" + dpv.tag_of(iso(2)), "strike_price": "4000", "mark_price": "10", "quotes": {}})
            return Resp({"success": True, "result": rows})
        raise AssertionError("unexpected " + url)


print("2. CANDLES: NEWEST FIRST FROM DELTA, OLDEST FIRST FOR THE ENGINE, PAGED")
fh = FakeHTTP()
p = dpv.DeltaDataProvider(session=fh)
df = p.get_ohlc("BTC", "15m", lookback_days=2)
check("two days of 15-minute candles, ascending, in IST, with the perpetual's volume",
      190 <= len(df) <= 194 and df.index.is_monotonic_increasing and str(df.index.tz) == "Asia/Kolkata"
      and list(df.columns) == ["Open", "High", "Low", "Close", "Volume"] and float(df["Volume"].iloc[-1]) == 12.5, len(df))
check("one page was enough for two days", sum(1 for u, _ in fh.calls if u.endswith("/candles")) == 1)
fh.calls.clear()
df = p.get_ohlc("BTC", "15m", lookback_days=30)
check("thirty days need two pages, joined without a duplicate",
      sum(1 for u, _ in fh.calls if u.endswith("/candles")) == 2 and 2870 <= len(df) <= 2882
      and not df.index.duplicated().any(), (len(df), sum(1 for u, _ in fh.calls if u.endswith("/candles"))))
diffs = df.index.to_series().diff().dropna().unique()
check("...every bar fifteen minutes after the last, no gap at the page join", len(diffs) == 1 and diffs[0] == pd.Timedelta(minutes=15), diffs)
try:
    p.get_ohlc("BTC", "15minute")
    check("an unknown interval is refused", False)
except ValueError:
    check("an unknown interval is refused", True)

print("3. SPOT AND THE CHAIN, IN DOLLARS")
check("spot is the index's spot_price", p.spot("BTC") == 80400.5)
check("the index's own symbol stands in for a token", p.index_token("BTC") == ".DEXBTUSD" and p.equity_tokens(["X"]) == {})
config.MAX_SPREAD_PCT = 5.0
dpv._PICK.clear(); dpv._PICK_AT.clear(); dpv._SINCE.clear(); dpv._BAD.clear()
exp = p.pick_expiry("BTC")
check("the expiry: the nearest with more than a day to run whose ATM spread is under the limit - the 12%-wide daily is skipped",
      exp == iso(2), (exp, iso(1), iso(2)))
chain = p.get_option_chain("BTC")
check("the chain in the engine's shape: strikes with call/put OI and dollar marks, PCR, max pain, walls",
      chain and chain["expiry"] == iso(2) and chain["spot"] == 80400.5 and len(chain["strikes"]) == 5
      and chain["strikes"][1]["call_ltp"] == 900.0 and chain["strikes"][1]["call_bid"] == 886.5 and chain["pcr"] == 2.0
      and chain["max_pain"] in (79600, 80000, 80400, 80800, 81200), chain and {k: chain[k] for k in ("expiry", "spot", "pcr")})
check("ETH's contracts are not in BTC's chain", all(80000 - 1000 < s["strike"] < 82000 for s in chain["strikes"]))
check("get_expiry_dates: every expiry listed, ISO, sorted", p.get_expiry_dates("BTC") == sorted([iso(1), iso(2), iso(9)]))
check("option_instrument: Delta's symbol for the engine's CE/PE, on the chosen expiry",
      p.option_instrument("BTC", 80000, "CE") == sym("C", 80000, iso(2)) and p.option_instrument("BTC", 80400.0, "PE", iso(9)) == sym("P", 80400, iso(9)))
check("a strike Delta does not list: None, never a guess", p.option_instrument("BTC", 80200, "CE") is None)
check("the chain is cached for thirty seconds", sum(1 for u, _ in fh.calls if u.endswith("/v2/tickers")) == 1)
n = len(fh.calls)
fh.spread[iso(2)] = 9.0                       # the chosen expiry widens
p._chain_cache.clear(); dpv._PICK_AT.clear()
for _ in range(3):
    dpv._PICK_AT.clear(); p._chain_cache.clear()
    check_exp = p.pick_expiry("BTC")
check("sticky: a widened spread is not left on one wide reading", check_exp == iso(2))
dpv._PICK_AT.clear(); p._chain_cache.clear()
check("...but is left after four checks in a row", p.pick_expiry("BTC") == iso(9), p.pick_expiry("BTC"))

print("4. THE SOCKET'S MESSAGES")
st = dpv.DeltaStreamer()
check("the interface feeds.py relies on, and quotes in dollars", all(hasattr(st, m) for m in
      ("start", "subscribe", "subscribe_index", "subscribe_ticker", "subscribe_tickers", "index_price", "mark_usd", "book", "forming_bar",
       "age_seconds", "stop")) and st.QUOTES_IN_COIN is False)
st.subscribe_index(".DEXBTUSD"); st.subscribe_ticker("C-BTC-80000-250926")
check("subscriptions are kept for the connect, as (channel, symbol)",
      st._subs == {("spot_price", ".DEXBTUSD"), ("v2/ticker", "C-BTC-80000-250926")})
t0 = 1_800_000_000.0
st._handle({"type": "spot_price", "symbol": ".DEXBTUSD", "price": 80461.1}, now=t0)
st._handle({"type": "spot_price", "symbol": ".DEXBTUSD", "price": 80470.0}, now=t0 + 5)
check("spot_price moves the index and builds the forming 15-minute bar",
      st.index_price(".DEXBTUSD") == 80470.0 and st.forming_bar(".DEXBTUSD") == {"start": int(t0 // 900) * 900, "o": 80461.1, "h": 80470.0, "l": 80461.1, "c": 80470.0})
st._handle({"type": "v2/ticker", "symbol": "C-BTC-80000-250926", "mark_price": "1510.25", "close": 1503.0, "oi": "16.442",
            "quotes": {"best_bid": "1500", "best_ask": "1520"}, "spot_price": "80461.1"}, now=t0 + 6)
check("v2/ticker keeps the mark, the quote, the last and the OI - in dollars, no conversion",
      st.mark_usd("C-BTC-80000-250926", ".DEXBTUSD") == 1510.25 and st.book("C-BTC-80000-250926")["bid"] == 1500.0
      and st.book("C-BTC-80000-250926")["ask"] == 1520.0 and st.book("C-BTC-80000-250926")["oi"] == 16.442)
check("a heartbeat or an unknown message moves nothing", not st._handle({"type": "heartbeat"}) and not st._handle({"type": "subscriptions"}))
check("a contract never seen: no mark, no book", st.mark_usd("P-BTC-1-010101") is None and st.book("P-BTC-1-010101") is None)
st._tick["C-BTC-80000-250926"]["at"] = time.time() - 100
check("older than max_age is not live", st.book("C-BTC-80000-250926", max_age=20) is None)
frames = []
class FakeWS:
    def send(self, text): frames.append(json.loads(text))
st._ws = FakeWS(); st.connected = True
st.subscribe([("v2/ticker", "P-BTC-80000-250926")])
check("a new subscription on a live socket is sent in Delta's format",
      frames and frames[-1] == {"type": "subscribe", "payload": {"channels": [{"name": "v2/ticker", "symbols": ["P-BTC-80000-250926"]}]}}, frames[-1:])
frames.clear()
st.subscribe([("v2/ticker", "P-BTC-80000-250926")])
check("...and never twice", frames == [])

print("DELTA PROVIDER TEST PASSED" if not fails else f"DELTA PROVIDER TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
