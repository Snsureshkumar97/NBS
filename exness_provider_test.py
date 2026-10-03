#!/usr/bin/env python3
"""exness_provider.py - hand-traced with a fake HTTP session: no network, no token."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import config
import exness_provider as ep

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


class Resp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body
    def json(self):
        return self._b
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class Session:
    """A fake MetaApi. A route's fragment must END the URL path - "/symbols" is also a
    substring of every price and candle URL, so a plain substring match would answer
    those with the symbol list."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []
    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        for frag, fn in self.routes:
            if url.endswith(frag):
                return fn(url, params or {})
        return Resp(404, {})


SECRET = "tok-NOT-A-REAL-TOKEN-123"
for k in ("METAAPI_TOKEN", "METAAPI_ACCOUNT_ID", "METAAPI_REGION", "EXNESS_SYMBOL_SUFFIX"):
    os.environ.pop(k, None)
config.INSTRUMENTS.setdefault("BTC", {})["exness_symbol"] = config.INSTRUMENTS["BTC"].get("exness_symbol", "BTCUSD")
config.INSTRUMENTS.setdefault("GOLD", {})["exness_symbol"] = config.INSTRUMENTS["GOLD"].get("exness_symbol", "XAUUSD")

print("1. NOT CONNECTED YET - A CLEAR MESSAGE, NO HTTP AT ALL")
s = Session([])
p = ep.ExnessMetaApiProvider(session=s)
try:
    p.quote("BTC"); raised = None
except ep.NotConfigured as exc:
    raised = str(exc)
check("without METAAPI_TOKEN/ACCOUNT_ID it says Exness is not connected", raised and "not connected" in raised, raised)
check("...and made no request", s.calls == [])
check("configured() is False", ep.configured() is False)
os.environ.update({"METAAPI_TOKEN": SECRET, "METAAPI_ACCOUNT_ID": "acc-1", "METAAPI_REGION": "london"})
check("configured() True once both are set", ep.configured() is True)

print("2. THE ACCOUNT'S OWN SYMBOL NAMES (Standard 'm', Pro none)")
std = Session([("/symbols/BTCUSDm/current-price", lambda u, q: Resp(200, {"symbol": "BTCUSDm", "bid": 84000.0, "ask": 84015.0})),
               ("/symbols", lambda u, q: Resp(200, ["EURUSDm", "XAUUSDm", "BTCUSDm"]))])
p = ep.ExnessMetaApiProvider(session=std)
check("a Standard account resolves BTC -> BTCUSDm, GOLD -> XAUUSDm",
      p.broker_symbol("BTC") == "BTCUSDm" and p.broker_symbol("GOLD") == "XAUUSDm")
check("the symbol list is read once and cached", sum(1 for c in std.calls if c[0].endswith("/symbols")) == 1)
pro = Session([("/symbols", lambda u, q: Resp(200, ["BTCUSD", "XAUUSD"]))])
check("a Pro account (no suffix) resolves BTC -> BTCUSD", ep.ExnessMetaApiProvider(session=pro).broker_symbol("BTC") == "BTCUSD")
os.environ["EXNESS_SYMBOL_SUFFIX"] = "c"
check("EXNESS_SYMBOL_SUFFIX forces a suffix without asking", ep.ExnessMetaApiProvider(session=Session([])).broker_symbol("BTC") == "BTCUSDc")
os.environ.pop("EXNESS_SYMBOL_SUFFIX")
none = Session([("/symbols", lambda u, q: Resp(200, ["EURUSD"]))])
try:
    ep.ExnessMetaApiProvider(session=none).broker_symbol("BTC"); msg = None
except RuntimeError as exc:
    msg = str(exc)
check("an account without BTC says so plainly", msg and "not offered" in msg, msg)

print("3. A QUOTE - BID, ASK, MID, SPREAD - AND THE TOKEN ONLY EVER IN THE HEADER")
q = p.quote("BTC")
check("mid = (84,000 + 84,015)/2, spread 15", q["mid"] == 84007.5 and q["spread"] == 15.0, q)
url, params, hdr = [c for c in std.calls if "current-price" in c[0]][0]
check("the documented host for the account's region", url.startswith("https://mt-client-api-v1.london.agiliumtrade.ai/"
      "users/current/accounts/acc-1/symbols/BTCUSDm/current-price"), url)
check("token in the auth-token header", hdr.get("auth-token") == SECRET)
check("token NOT in any URL or query string", all(SECRET not in c[0] and SECRET not in str(c[1]) for c in std.calls))
check("spot() is the mid", p.spot("BTC") == 84007.5)

print("4. ERRORS - 401, 404, AND A 429 THAT BACKS OFF")
for code, word in ((401, "401"), (404, "404")):
    sess = Session([("/symbols", lambda u, q: Resp(200, ["BTCUSD"])), ("current-price", lambda u, q, c=code: Resp(c, {}))])
    try:
        ep.ExnessMetaApiProvider(session=sess).quote("BTC"); m = None
    except RuntimeError as exc:
        m = str(exc)
    check(f"HTTP {code} becomes a readable error that does not contain the token", m and word in m and SECRET not in m, m)
rl = Session([("/symbols", lambda u, q: Resp(200, ["BTCUSD"])),
              ("current-price", lambda u, q: Resp(429, {"metadata": {"recommendedRetryTime":
                                                (pd.Timestamp.now(tz="UTC") + pd.Timedelta(seconds=30)).isoformat()}}))])
pr = ep.ExnessMetaApiProvider(session=rl)
try:
    pr.quote("BTC")
except RuntimeError:
    pass
n_before = len(rl.calls)
try:
    pr.quote("BTC"); m = None
except RuntimeError as exc:
    m = str(exc)
check("after a 429 it backs off for MetaApi's recommended time - the next call makes NO request",
      len(rl.calls) == n_before and m and "slow down" in m, (len(rl.calls), n_before, m))

print("5. CANDLES - PAGED BACKWARDS 1,000 AT A TIME, IST, CACHED")
now = pd.Timestamp.now(tz="UTC").floor("15min")
def candles(u, q):
    end = pd.Timestamp(q["startTime"]).floor("15min")
    out = []
    for k in range(int(q["limit"])):
        t = end - pd.Timedelta(minutes=15 * k)
        if t < now - pd.Timedelta(days=20):
            break
        out.append({"time": t.isoformat(), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "tickVolume": 7})
    return Resp(200, out)
cs = Session([("/symbols", lambda u, q: Resp(200, ["XAUUSDm"])), ("/candles", candles)])
pc = ep.ExnessMetaApiProvider(session=cs)
df = pc.get_ohlc("GOLD", "15m", lookback_days=12)          # 12 days = 1,152 bars -> needs 2 pages
pages = [c for c in cs.calls if "/candles" in c[0]]
check("12 days needed two pages of <=1,000", len(pages) == 2, len(pages))
check("the documented market-data host and path",
      pages[0][0] == "https://mt-market-data-client-api-v1.london.agiliumtrade.ai/users/current/accounts/acc-1/"
                     "historical-market-data/symbols/XAUUSDm/timeframes/15m/candles", pages[0][0])
check("the page limit is 1,000", pages[0][1].get("limit") == 1000)
check("only the last 12 days kept, oldest first, no duplicates", df.index.is_monotonic_increasing and df.index.is_unique
      and df.index.min() >= (now - pd.Timedelta(days=12)).tz_convert("Asia/Kolkata") - pd.Timedelta(minutes=15))
check("IST index, the feed's own columns, tick volume as Volume",
      str(df.index.tz) == "Asia/Kolkata" and list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
      and (df["Volume"] == 7).all())
pc.get_ohlc("GOLD", "15m", lookback_days=12)
check("a second ask within the cache window makes no request", len([c for c in cs.calls if "/candles" in c[0]]) == 2)

print("6. NO OPTIONS AT EXNESS - HONEST EMPTY ANSWERS")
check("no chain, no expiries, no pick, no contracts",
      pc.get_option_chain("BTC") is None and pc.get_expiry_dates("BTC") == [] and pc.pick_expiry("BTC") is None
      and pc.option_instrument("BTC", 84000, "CE") is None and pc.option_token("BTC", 84000, "CE") is None)
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "exness_provider.py")).read()
check("no order code here - this module only reads prices", all(w not in src for w in ("/trade", "createMarket", "actionType")))

print("7. ONE SHARED POLLER - LIVE QUOTES AND THE FORMING 15-MINUTE BAR")
seq = iter([(84000.0, 84010.0), (84100.0, 84110.0), (83950.0, 83960.0)])
def price(u, q):
    b, a = next(seq)
    return Resp(200, {"symbol": "BTCUSDm", "bid": b, "ask": a})
ps = Session([("current-price", price), ("/symbols", lambda u, q: Resp(200, ["BTCUSDm"]))])
pol = ep._Poller(provider=ep.ExnessMetaApiProvider(session=ps))
pol.keys.add("BTC")
t0 = 1_790_000_100.0 - (1_790_000_100.0 % 900) + 60      # one minute into a 15-minute bar
for k in range(3):
    pol.poll_once(now=t0 + k)
st = ep.ExnessStreamer(shared=pol)
check("streamer start() is True once configured", st.start() is True and st.connected)
check("index_price by the instrument KEY gives the latest mid (83,955)", st.index_price("BTC") == 83955.0, st.index_price("BTC"))
bar = st.forming_bar("BTC")
check("the forming bar: open 84,005, high 84,105, low 83,955, close 83,955",
      bar and bar["open"] == 84005.0 and bar["high"] == 84105.0 and bar["low"] == 83955.0 and bar["close"] == 83955.0, bar)
check("quote() exposes the live spread (10)", st.quote("BTC")["spread"] == 10.0)
check("option marks/books are None", st.mark_usd("C-BTC-1") is None and st.book("C-BTC-1") is None)
os.environ.pop("METAAPI_TOKEN")
check("not configured -> start() is False with the reason", ep.ExnessStreamer(shared=pol).start() is False)

print()
if fails:
    print(f"EXNESS PROVIDER TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("EXNESS PROVIDER TEST PASSED")
