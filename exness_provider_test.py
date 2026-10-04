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
      len(rl.calls) == n_before and m and "slow down" in m and "(prices)" in m, (len(rl.calls), n_before, m))

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
check("...in the keys feeds._df_with_live_bar actually reads (o/h/l/c, like DeltaStreamer)",
      bar and (bar["o"], bar["h"], bar["l"], bar["c"]) == (84005.0, 84105.0, 83955.0, 83955.0) and bar["start"] % 900 == 0)
check("quote() exposes the live spread (10)", st.quote("BTC")["spread"] == 10.0)
check("option marks/books are None", st.mark_usd("C-BTC-1") is None and st.book("C-BTC-1") is None)
os.environ.pop("METAAPI_TOKEN")
check("not configured -> start() is False with the reason", ep.ExnessStreamer(shared=pol).start() is False)
os.environ["METAAPI_TOKEN"] = SECRET

print("8. CANDLE TOP-UPS - ONE SMALL REQUEST AFTER THE FIRST LOAD, OTHER TIMEFRAMES, BAD ONES REFUSED")
c8 = Session([("/symbols", lambda u, q: Resp(200, ["BTCUSDm"])), ("/candles", candles)])
p8 = ep.ExnessMetaApiProvider(session=c8)
p8.get_ohlc("BTC", "15m", lookback_days=12)
first = [c for c in c8.calls if "/candles" in c[0]]
key = ("BTCUSDm", "15m")
fetched_at, frame, held = p8._candles[key]
p8._candles[key] = (fetched_at - 60, frame, held)                  # the cache window has passed
df8 = p8.get_ohlc("BTC", "15m", lookback_days=12)
top = [c for c in c8.calls if "/candles" in c[0]][len(first):]
check("after the cache window: ONE request for the bars since the last (limit 5, not 1,000)",
      len(top) == 1 and top[0][1]["limit"] == 5, [c[1].get("limit") for c in top])
check("...and the history is still 12 days, unique, in order",
      df8.index.is_unique and df8.index.is_monotonic_increasing and len(df8) >= 12 * 96 - 2, len(df8))
p8.get_ohlc("BTC", "15m", lookback_days=5)
check("a SHORTER ask is served from the same history - no request",
      len([c for c in c8.calls if "/candles" in c[0]]) == len(first) + 1)
p8.get_ohlc("BTC", "5m", lookback_days=1)
last = [c for c in c8.calls if "/candles" in c[0]][-1]
check("5m asks MetaApi's own 5m timeframe", last[0].endswith("/timeframes/5m/candles"), last[0])
p8.get_ohlc("BTC", "1d", lookback_days=3)
check("1d too", [c for c in c8.calls if "/candles" in c[0]][-1][0].endswith("/timeframes/1d/candles"))
n_before = len([c for c in c8.calls if "/candles" in c[0]])
p8.get_ohlc("BTC", "1d", lookback_days=250)
lim = [c for c in c8.calls if "/candles" in c[0]][n_before][1]["limit"]          # this load's FIRST request
check("a year of daily candles asks for the 252 it needs, not 1,000 (1,000 timed out at MetaApi)", lim == 252, lim)
try:
    p8.get_ohlc("BTC", "7m"); bad = None
except ValueError as exc:
    bad = str(exc)
check("an interval MetaApi does not have is refused, not guessed", bad and "unknown interval" in bad, bad)

print("9. THE ACCOUNT SWITCHED OFF (504) - A PLAIN MESSAGE")
s9 = Session([("/symbols", lambda u, q: Resp(200, ["BTCUSD"])), ("current-price", lambda u, q: Resp(504, {}))])
try:
    ep.ExnessMetaApiProvider(session=s9).quote("BTC"); m9 = None
except RuntimeError as exc:
    m9 = str(exc)
check("504 says the account is not connected and to check it is Deployed", m9 and "not connected to the broker" in m9
      and "Deployed" in m9 and SECRET not in m9, m9)

print("10. A CLOSED MARKET (GOLD AT THE WEEKEND) - NO FLAT BARS, AND ITS AGE IS KNOWN")
friday = "2026-10-03T02:15:00.000Z"
s10 = Session([("current-price", lambda u, q: Resp(200, {"symbol": "XAUUSDm", "bid": 4140.3, "ask": 4140.56, "time": friday})),
               ("/symbols", lambda u, q: Resp(200, ["XAUUSDm"]))])
pol10 = ep._Poller(provider=ep.ExnessMetaApiProvider(session=s10))
pol10.keys.add("GOLD")
sat = pd.Timestamp("2026-10-03T15:30:00Z").timestamp()           # Saturday afternoon, 13h15 after the last tick
pol10.poll_once(now=sat)
st10 = ep.ExnessStreamer(shared=pol10)
check("the last price is still readable (4,140.43)", round(st10.index_price("GOLD"), 2) == 4140.43)
check("but NO forming bar is built from it", st10.forming_bar("GOLD") is None)
age = pol10.price_age("GOLD", now=sat)
check("price_age = 13h15 = 47,700 s, over CFD_STALE_QUOTE_S", age == 47700.0 and age > ep.STALE_QUOTE_S, age)
check("the streamer exposes the same age", st10.price_age("GOLD") is not None and st10.price_age("GOLD") >= 47700.0)
live = Session([("current-price", lambda u, q: Resp(200, {"symbol": "XAUUSDm", "bid": 4140.3, "ask": 4140.56,
                                                            "time": pd.Timestamp(sat - 2, unit="s", tz="UTC").isoformat()})),
                ("/symbols", lambda u, q: Resp(200, ["XAUUSDm"]))])
pol11 = ep._Poller(provider=ep.ExnessMetaApiProvider(session=live))
pol11.keys.add("GOLD")
pol11.poll_once(now=sat)
check("a 2-second-old tick IS live: a bar is built, age 2 s",
      ep.ExnessStreamer(shared=pol11).forming_bar("GOLD") is not None and pol11.price_age("GOLD", now=sat) == 2.0)
check("ONE shared provider for the whole server", ep.shared() is ep.shared())

print("8c. RIGHT AFTER A CLOSE: THE NEW CANDLE IS ASKED FOR EVERY 2 s, NOT EVERY 30 s (the user, 4 Oct 2026: \"every 15")
print("    minutes the page is reloading for rsi extreme\" - the rule read 'waiting' until the 30-second cache ran out)")
boundary = pd.Timestamp("2026-10-05 10:45", tz="UTC")
def lagging(u, q):                       # MetaApi has not published the new candle yet: its newest is the one that closed
    end = pd.Timestamp(q["startTime"]).floor("15min") - pd.Timedelta(minutes=15)
    out = []
    for k in range(int(q["limit"])):
        t = end - pd.Timedelta(minutes=15 * k)
        if t < boundary - pd.Timedelta(days=20):
            break
        out.append({"time": t.isoformat(), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "tickVolume": 7})
    return Resp(200, out)
c9 = Session([("/symbols", lambda u, q: Resp(200, ["BTCUSDm"])), ("/candles", lagging)])
p9 = ep.ExnessMetaApiProvider(session=c9)
_real_now = ep._utcnow
try:
    ep._utcnow = lambda: boundary + pd.Timedelta(seconds=40)          # 40 s after the 10:45 close
    p9.get_ohlc("BTC", "15m", lookback_days=3)
    n9 = len([c for c in c9.calls if "/candles" in c[0]])
    k9 = ("BTCUSDm", "15m")
    t9, f9, h9 = p9._candles[k9]
    p9._candles[k9] = (t9 - 3, f9, h9)                                # 3 s old: inside the 30-s cache
    p9.get_ohlc("BTC", "15m", lookback_days=3)
    check("the new candle is missing and 3 s have passed: asked again at once (not after 30 s)",
          len([c for c in c9.calls if "/candles" in c[0]]) == n9 + 1)
    t9, f9, h9 = p9._candles[k9]
    p9._candles[k9] = (t9 - 1, f9, h9)                                # 1 s old
    p9.get_ohlc("BTC", "15m", lookback_days=3)
    check("...but never more than every 2 s", len([c for c in c9.calls if "/candles" in c[0]]) == n9 + 1)
    ep._utcnow = lambda: boundary + pd.Timedelta(minutes=12, seconds=40)   # 12 min on, still no new candle: closed market
    t9, f9, h9 = p9._candles[k9]
    p9._candles[k9] = (t9 - 3, f9, h9)
    p9.get_ohlc("BTC", "15m", lookback_days=3)
    check("a market with no new candle for 10+ minutes (gold at the weekend) keeps the normal 30-s cache",
          len([c for c in c9.calls if "/candles" in c[0]]) == n9 + 1)
finally:
    ep._utcnow = _real_now

print("8d. A REFUSAL ON ONE SERVICE NEVER BLOCKS THE OTHER; A FAILED TOP-UP SERVES THE CANDLES ALREADY HELD (the user,")
print("    4 Oct 2026: 'i always getting runtime error for 15 minutes' - the card read 'no candles for the rule: RuntimeError')")
mode = {"candles": "ok"}
def flaky_candles(u, q):
    if mode["candles"] == "429":
        return Resp(429, {"metadata": {"recommendedRetryTime": (pd.Timestamp.now(tz="UTC") + pd.Timedelta(seconds=30)).isoformat()}})
    return candles(u, q)
sd = Session([("/symbols", lambda u, q: Resp(200, ["BTCUSDm"])), ("/candles", flaky_candles),
              ("current-price", lambda u, q: Resp(200, {"symbol": "BTCUSDm", "bid": 84000.0, "ask": 84010.0}))])
pd_ = ep.ExnessMetaApiProvider(session=sd)
held = pd_.get_ohlc("BTC", "15m", lookback_days=3)
mode["candles"] = "429"
kd = ("BTCUSDm", "15m")
td, fd, hd = pd_._candles[kd]
pd_._candles[kd] = (td - 60, fd, hd)                                # time to top up - and the top-up is refused
try:
    got = pd_.get_ohlc("BTC", "15m", lookback_days=3); err = None
except RuntimeError as exc:
    got, err = None, str(exc)
check("a refused candle top-up serves the candles already held - no error reaches the rule",
      err is None and got is not None and len(got) == len(held) and got.index.equals(held.index), err)
try:
    q_ = pd_.quote("BTC"); qerr = None
except RuntimeError as exc:
    q_, qerr = None, str(exc)
check("...and the live PRICE still comes through: the candles' back-off is not the prices'",
      qerr is None and q_ and q_["bid"] == 84000.0, qerr)
n_c = len([c for c in sd.calls if "/candles" in c[0]])
td, fd, hd = pd_._candles[kd]
pd_._candles[kd] = (td - 60, fd, hd)
pd_.get_ohlc("BTC", "15m", lookback_days=3)
check("...while the candles' back-off lasts, no new candle request is made", len([c for c in sd.calls if "/candles" in c[0]]) == n_c)
pd_._backoff_until.clear(); mode["candles"] = "ok"
td, fd, hd = pd_._candles[kd]
pd_._candles[kd] = (td - 60, fd, hd)
pd_.get_ohlc("BTC", "15m", lookback_days=3)
check("...and once it is over, the next call tops up again", len([c for c in sd.calls if "/candles" in c[0]]) == n_c + 1)
fresh_p = ep.ExnessMetaApiProvider(session=sd)
mode["candles"] = "429"
try:
    fresh_p.get_ohlc("BTC", "15m", lookback_days=3); err2 = None
except RuntimeError as exc:
    err2 = str(exc)
check("a FIRST load that is refused still says so - there is nothing held to serve", err2 and "candles" in err2, err2)
mode["candles"] = "ok"
pq = Session([("/symbols", lambda u, q: Resp(200, ["BTCUSDm"])), ("/candles", candles),
              ("current-price", lambda u, q: Resp(429, {"metadata": {}}))])
pp = ep.ExnessMetaApiProvider(session=pq)
try:
    pp.quote("BTC")
except RuntimeError:
    pass
try:
    pp.get_ohlc("BTC", "15m", lookback_days=2); err3 = None
except RuntimeError as exc:
    err3 = str(exc)
check("the other way round: a refused PRICE does not block the candles", err3 is None, err3)

print("9. THE PRICE IS ASKED EVERY SECOND (the user, 4 Oct 2026: \"yes make it every second\")")
check("POLL_S is one second", ep.POLL_S == 1.0, ep.POLL_S)
pl = ep._Poller(provider=object(), poll_s=ep.POLL_S)
pl.poll_once = lambda now=None: None                     # a pass that takes no time at all
slept = []
class _Stop(Exception):
    pass
def _sleep(sec):
    slept.append(sec)
    raise _Stop
_real_sleep = ep.time.sleep
ep.time.sleep = _sleep
try:
    pl._run()
except _Stop:
    pass
finally:
    ep.time.sleep = _real_sleep
check("...a quick pass then sleeps the rest of that second, so the next ask is a second after the last",
      len(slept) == 1 and 0.9 < slept[0] <= 1.0, slept)

print()
if fails:
    print(f"EXNESS PROVIDER TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("EXNESS PROVIDER TEST PASSED")
