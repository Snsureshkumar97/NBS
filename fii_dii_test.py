#!/usr/bin/env python3
"""FII/DII net flow (fii_dii.py): parsing NSE's own JSON shape, the market-wide
cache (fresh, reused, refreshed, kept through an outage, finally given up on),
and net_lean(). Fakes only - no real HTTP call is made."""
import sys
import tempfile
import os

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fii_dii as fd

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def fresh():
    fd._cache, fd._cache_at = None, 0.0


class Resp:
    def __init__(self, body, status=200):
        self._body, self.status = body, status
    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")
    def json(self):
        return self._body


NSE_SHAPE = [{"buyValue": "14098.64", "category": "DII", "date": "21-Sep-2026", "netValue": "2797.27", "sellValue": "11301.37"},
            {"buyValue": "10637.17", "category": "FII/FPI", "date": "21-Sep-2026", "netValue": "-576.2", "sellValue": "11213.37"}]


class FakeSession:
    def __init__(self, body=NSE_SHAPE, status=200, boom=None):
        self.body, self.status, self.boom, self.calls = body, status, boom, 0
    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        if self.boom:
            raise self.boom
        assert url == fd.URL and headers.get("User-Agent"), (url, headers)
        return Resp(self.body, self.status)


print("1. PARSING NSE'S OWN SHAPE")
r = fd._parse(NSE_SHAPE)
check("FII/FPI and DII rows become fii and dii, buy/sell/net kept as given",
      r == {"date": "21-Sep-2026", "fii": {"buy_cr": 10637.17, "sell_cr": 11213.37, "net_cr": -576.2},
            "dii": {"buy_cr": 14098.64, "sell_cr": 11301.37, "net_cr": 2797.27}}, r)
check("a category NSE might spell differently (FII, not FII/FPI) still lands on fii",
      fd._parse([{"category": "FII", "date": "1-Jan-2026", "buyValue": "1", "sellValue": "2", "netValue": "-1"},
                {"category": "DII", "date": "1-Jan-2026", "buyValue": "3", "sellValue": "1", "netValue": "2"}])["fii"]["net_cr"] == -1.0)
check("a missing net is computed from buy minus sell, not dropped",
      fd._parse([{"category": "FII", "date": "1-Jan-2026", "buyValue": "5", "sellValue": "2"},
                {"category": "DII", "date": "1-Jan-2026", "buyValue": "3", "sellValue": "1", "netValue": "2"}])["fii"]["net_cr"] == 3.0)
check("missing either row entirely gives no reading, not a half one",
      fd._parse([NSE_SHAPE[0]]) is None and fd._parse([]) is None and fd._parse(None) is None)
check("a row with no usable numbers is skipped rather than crashing the parse",
      fd._parse([{"category": "DII", "date": "1-Jan-2026", "buyValue": "x", "sellValue": "y"},
                {"category": "FII", "date": "1-Jan-2026", "buyValue": "1", "sellValue": "2", "netValue": "-1"}]) is None)
check("an unrecognised category (an index total NSE sometimes adds) is ignored, not mistaken for FII or DII",
      fd._parse(NSE_SHAPE + [{"category": "TOTAL", "date": "21-Sep-2026", "buyValue": "1", "sellValue": "1", "netValue": "0"}])
      == fd._parse(NSE_SHAPE))
check("the match is FII at the start of the category, not any category that happens to contain an F",
      fd._parse([{"category": "FUND", "date": "1-Jan-2026", "buyValue": "9", "sellValue": "1", "netValue": "8"},
                {"category": "DII", "date": "1-Jan-2026", "buyValue": "3", "sellValue": "1", "netValue": "2"}]) is None)

print("2. THE MARKET-WIDE CACHE")
fresh()
s = FakeSession()
r1 = fd.reading(session=s, now=1000.0)
check("the first call fetches, and says it is fresh", s.calls == 1 and r1["fetched_at"] == 1000.0 and r1["stale"] is False and r1["date"] == "21-Sep-2026")
r2 = fd.reading(session=s, now=1000.0 + fd.CACHE_TTL_S - 1)
check("inside the TTL, no second fetch - the cached reading is served, still not `stale`", s.calls == 1 and r2["date"] == "21-Sep-2026" and r2["stale"] is False)
r3 = fd.reading(session=s, now=1000.0 + fd.CACHE_TTL_S + 1)
check("past the TTL, it asks NSE again", s.calls == 2)
fresh()
s2 = FakeSession()
fd.reading(session=s2, now=3000.0)             # one caller fetches...
s3 = FakeSession()
r4 = fd.reading(session=s3, now=3000.5)         # ...a completely different caller, moments later
check("a second, independent caller shares the one cache - the market-wide point of it",
      r4["fetched_at"] == 3000.0 and s3.calls == 0, (r4, s3.calls))

print("3. AN OUTAGE KEEPS THE LAST GOOD READING, MARKED STALE")
fresh()
fd.reading(session=FakeSession(), now=5000.0)
boom_session = FakeSession(boom=RuntimeError("connection reset"))
r5 = fd.reading(session=boom_session, now=5000.0 + fd.CACHE_TTL_S + 1)
check("a fetch failure past the TTL still returns the old reading, but flagged stale",
      r5 is not None and r5["date"] == "21-Sep-2026" and r5["fetched_at"] == 5000.0 and r5["stale"] is True)
check("...and it did try - this was not served from an unexpired cache", boom_session.calls == 1)
r6 = fd.reading(session=FakeSession(status=500), now=5000.0 + fd.CACHE_TTL_S + 2)
check("an HTTP error status behaves the same as a network failure", r6 is not None and r6["stale"] is True)
r7 = fd.reading(session=FakeSession(body=[{"category": "DII", "buyValue": "1"}]), now=5000.0 + fd.CACHE_TTL_S + 3)
check("a response that parses to nothing is treated the same as a failure - the old reading, marked stale", r7 is not None and r7["stale"] is True)

print("4. NOTHING EVER CACHED, AND THE FETCH FAILS")
fresh()
check("no reading at all, not an exception", fd.reading(session=FakeSession(boom=RuntimeError("dns")), now=9000.0) is None)

print("5. A CACHE TOO OLD TO TRUST IS DROPPED, NOT SERVED FOREVER STALE")
fresh()
fd.reading(session=FakeSession(), now=1_000_000.0)
r8 = fd.reading(session=FakeSession(boom=RuntimeError("down")), now=1_000_000.0 + fd.STALE_AFTER_S - 1)
check("just under the stale limit, the old reading still comes back", r8 is not None)
r9 = fd.reading(session=FakeSession(boom=RuntimeError("down")), now=1_000_000.0 + fd.STALE_AFTER_S + 1)
check("past it, nothing is served rather than a reading days old with no warning on screen", r9 is None)

print("6b. IN THE BOT'S CONTEXT - INDIAN INDICES ONLY, NEVER CRYPTO")
import json
import market_bot as mb
snap = {"feed": "ok", "indices": {"BTC": {"spot": 85000.0}, "NIFTY": {"spot": 23400.0}}, "why": {}, "tickets": {},
        "session": {}, "fii_dii": {"date": "21-Sep-2026", "fii": {"net_cr": 500, "buy_cr": 0, "sell_cr": 0},
                                    "dii": {"net_cr": 300, "buy_cr": 0, "sell_cr": 0}, "stale": False}}
cn = json.loads(mb.build_context(snap, "nse_index", "NIFTY", user=None))
cb = json.loads(mb.build_context(snap, "crypto", "BTC", user=None))
check("the Indian context carries it", cn.get("fii_dii", {}).get("fii", {}).get("net_cr") == 500)
check("...but the crypto context never does, even though the same snapshot carried it", cb.get("fii_dii") is None, cb.get("fii_dii"))
check("no reading yet is None, not a missing key", json.loads(mb.build_context(dict(snap, fii_dii=None), "nse_index", "NIFTY", user=None)).get("fii_dii") is None)
for name, prompt in (("Ask TradePicker", mb.SYSTEM), ("the AI desk", mb.DESK_SYSTEM)):
    check(f"{name} is told what it is, that it is one number a day, and that a stale reading is marked",
          "fii_dii (Indian indices only" in prompt and "net_cr" in prompt and "one number a day" in prompt and "stale true" in prompt)

print("6. net_lean()")
mk = lambda f, d: {"date": "x", "fii": {"net_cr": f, "buy_cr": 0, "sell_cr": 0}, "dii": {"net_cr": d, "buy_cr": 0, "sell_cr": 0}}
check("both sides in agree, both out is -1, an exact offset is 0, and no reading is None",
      fd.net_lean(mk(300, 200)) == 1 and fd.net_lean(mk(-300, -200)) == -1 and fd.net_lean(mk(300, -300)) == 0
      and fd.net_lean(None) is None)
check("FII and DII pulling opposite ways: the combined net decides, not either one alone",
      fd.net_lean(mk(800, -300)) == 1 and fd.net_lean(mk(-800, 300)) == -1)

print("FII/DII TEST PASSED" if not fails else f"FII/DII TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
