#!/usr/bin/env python3
"""Bitcoin on the Greeks tab (the venue's own greeks and IV) and the contract
chart (Delta's option candles): the provider's two new methods and both page
handlers, on fakes only."""
import datetime as dt
import json
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
config.ENABLE_CRYPTO = True            # the crypto market is env-gated; this test is about it
import delta_provider as dpv
import feeds
import web_server

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


TODAY = dt.datetime.now(dt.timezone.utc).date()
EXP = (TODAY + dt.timedelta(days=2)).isoformat()
def sym(kind, k):
    return f"{kind}-BTC-{k}-{dpv.tag_of(EXP)}"


class Resp:
    def __init__(self, body): self._body = body
    def raise_for_status(self): pass
    def json(self): return self._body


class FakeHTTP:
    def __init__(self):
        self.calls = []
    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        p = params or {}
        if url.endswith("/v2/history/candles"):
            end, start = int(p["end"]), int(p["start"])
            ts = list(range(end - end % 900, max(start, end - 900 * 50), -900))
            return Resp({"success": True, "result": [{"time": t, "open": 500.0, "high": 520.0, "low": 490.0, "close": 510.0, "volume": 3.0} for t in ts]})
        if url.endswith("/v2/tickers"):
            rows = []
            for k in (79600, 80000, 80400, 80800):
                for kind in ("C", "P"):
                    d = 0.6 if kind == "C" else -0.4
                    rows.append({"symbol": sym(kind, k), "mark_price": "500", "close": 498, "oi": "2.0", "spot_price": "80400",
                                 "quotes": {"best_bid": "495", "best_ask": "505", "mark_iv": "0.4125"},
                                 "greeks": {"delta": str(d), "gamma": "0.00002", "theta": "-21.5", "vega": "70.1", "rho": "3"}})
            return Resp({"success": True, "result": rows})
        raise AssertionError(url)


print("1. THE PROVIDER: THE VENUE'S GREEKS RIDE WITH THE CHAIN; A CONTRACT'S CANDLES")
fh = FakeHTTP()
p = dpv.DeltaDataProvider(session=fh)
config.MAX_SPREAD_PCT = 5.0
dpv._PICK.clear(); dpv._PICK_AT.clear()
r = next(x for x in p._chain_rows("BTC") if x["strike"] == 80000 and x["kind"] == "C")
check("each chain row carries the venue's delta/gamma/theta/vega/rho and mark IV in percent",
      r["delta"] == 0.6 and r["gamma"] == 0.00002 and r["theta"] == -21.5 and r["vega"] == 70.1 and r["iv"] == 41.25, r)
check("option_token is the symbol", p.option_token("BTC", 80000, "CE", EXP) == sym("C", 80000))
df = p.candles_for_token(sym("C", 80000), interval="5minute", days=1)
check("candles_for_token: Kite's interval names are translated, lower-case columns with ts, oldest first",
      fh.calls[-1][1]["resolution"] == "5m" and fh.calls[-1][1]["symbol"] == sym("C", 80000)
      and list(df.columns) == ["ts", "open", "high", "low", "close", "volume"] and df["ts"].is_monotonic_increasing and len(df) == 50)
try:
    p.candles_for_token(sym("C", 80000), interval="2minute"); check("an unknown interval raises", False)
except ValueError:
    check("an unknown interval raises", True)


class FakeFeed:
    def __init__(self, prov): self.prov = prov
    def _provider(self): return self.prov, "ok", ""


def handler(market="crypto"):
    h = object.__new__(web_server.Handler)
    out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, code=code)
    h._current_market = lambda: market
    h._current_user = lambda: "me@example.invalid"
    return h, out


print("2. THE GREEKS TAB FOR BITCOIN")
orig = feeds.for_user
feeds.for_user = lambda user, market=None, start=True: FakeFeed(p)
try:
    h, out = handler()
    h._api_greeks("me@example.invalid", {"index": ["BTC"]})
    d = json.loads(out["body"])
    row = next(r for r in d["rows"] if r["strike"] == 80000)
    check("the same shape as the Indian tab, from the venue: rows with iv/delta/gamma/theta/vega/gxoi per side",
          d["index"] == "BTC" and d["expiry"] == EXP and d["spot"] == 80400.0 and row["ce"]["iv"] == 41.25
          and row["ce"]["delta"] == 0.6 and row["pe"]["delta"] == -0.4 and row["ce"]["gxoi"] == round(0.00002 * 2.0, 4), row)
    check("ATM, wings, skew and the gamma concentration are computed on those figures",
          d["atm"] == 80400 and d["atm_iv"] == 41.25 and d["solved"] == 8 and d["quotes"] == 8 and isinstance(d["concentration"], list))
    check("it says where the figures come from and that nothing is solved here", d["source"] == "Delta Exchange India"
          and "straight from the venue" in d["note"] and d["rate"] is None)
    check("minutes run to 12:00 UTC on the expiry day", 0 < d["minutes"] < 3 * 1440)

    class Coin:                       # a venue that quotes in the coin and publishes no greeks
        QUOTES_IN_COIN = True
        def _chain_rows(self, k): return []
    feeds.for_user = lambda user, market=None, start=True: FakeFeed(Coin())
    h, out = handler()
    h._api_greeks("me@example.invalid", {"index": ["BTC"]})
    check("a venue without greeks: an honest refusal, no rows", json.loads(out["body"])["rows"] == [] and "not publish" in json.loads(out["body"])["note"])
finally:
    feeds.for_user = orig

print("3. THE CONTRACT CHART FOR BITCOIN")
feeds.for_user = lambda user, market=None, start=True: FakeFeed(p)
try:
    h, out = handler()
    h._option_candles("me@example.invalid", "BTC/80000/CE", {"tf": ["5m"], "expiry": [EXP]})
    d = json.loads(out["body"])
    check("Delta's candles for the exact contract, on the chart's shape", not d.get("error") and d["index"] == "BTC"
          and len(d["candles"]) == 50 and d["candles"][-1][4] == 510.0, d.get("error") or len(d.get("candles") or []))
    h, out = handler()
    h._option_candles("me@example.invalid", "BTC/80200/CE", {"tf": ["5m"], "expiry": [EXP]})
    check("a strike Delta does not list: no contract, no guess", "No contract" in json.loads(out["body"]).get("error", ""))
finally:
    feeds.for_user = orig

print("DELTA PAGE TEST PASSED" if not fails else f"DELTA PAGE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
